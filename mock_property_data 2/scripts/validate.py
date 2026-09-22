"""Validate delivered files against the supplied contract and DB invariants."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from contract_validation import (contracts,check_type,require,ValidationError,
    timestamp,validate_listing,validate_search_envelope)
from generate import ROOT,FIELDS,HDB,CONDO,LANDED,SUBFIELDS,to_listing

def read(p):return json.loads(p.read_text())

def expected_screen_bucket(listing,profile):
    """Independent deterministic test oracle for this package's documented profile.

    No production screening function is supplied or replaced. Known hard failure
    wins over unknown; inactive always fails. Freshness is a review concern.
    A mismatching price period fails unless the application explicitly supplies
    a conversion policy (there is no such policy in this fixture).
    """
    f=profile['hard_constraints'];unknown=False;failed=False
    pairs=[(listing['transaction_type'],'sale' if profile['intent']=='buy' else 'rent'),
           (listing['listing_status'],'active'),(listing['price']['currency'],f['currency'])]
    if f['rental_scope'] is not None:pairs.append((listing['attributes']['listing_scope'],f['rental_scope']))
    if f['price_period'] is not None:pairs.append((listing['price']['period'],f['price_period']))
    for got,want in pairs:
        if got is None or got=='unknown':unknown=True
        elif got!=want:failed=True
    if f['max_price'] is not None:
        if listing['price']['status']!='known' or listing['price']['amount'] is None:unknown=True
        elif listing['price']['amount']>f['max_price']:failed=True
    if f['locations']:
        if listing['location_id'] is None:unknown=True
        elif listing['location_id'] not in f['locations']:failed=True
    if f['min_bedrooms'] is not None:
        if listing['bedrooms'] is None:unknown=True
        elif listing['bedrooms']<f['min_bedrooms']:failed=True
    return 'rejected' if failed else ('needs_verification' if unknown else 'eligible')

def validate_db(r):
    f=r['fields'];p=r['listing_key']
    require(set(f)==set(FIELDS),p+'.fields','requires exactly the 54 fields')
    bools=['ensuite_bathroom','owner_stays','utilities_included','wifi_included','visitors_allowed','pets_allowed']
    for k in bools:require(f[k] is None or type(f[k]) is bool,p+'.'+k,'tri-state boolean required')
    for k in ['price_sgd','area_sqft','bedrooms','bathrooms','lease_years','top_year','lease_commencement_year','land_area_sqft','built_up_area_sqft','storeys']:
        require(f[k] is None or (type(f[k]) is int and f[k]>=0),p+'.'+k,'integer or null')
    for k in ['raw_details','facilities']:
        require((k=='facilities' and f[k] is None) or (isinstance(f[k],list) and all(type(v) is str for v in f[k])),p+'.'+k,'string list required')
    require(f['extra_attributes']['is_synthetic'] is True,p+'.extra_attributes','must be synthetic')
    require((f['latitude'] is None)==(f['longitude'] is None),p+'.coordinates','paired coordinate nulls')
    if f['latitude'] is not None:require(-90<=f['latitude']<=90 and -180<=f['longitude']<=180,p+'.coordinates','coordinate bounds')
    if f['postal_code'] is not None:require(type(f['postal_code']) is str and len(f['postal_code'])==6 and f['postal_code'].isdigit(),p+'.postal_code','six-character string')
    require(timestamp(f['created_at'],p)<=timestamp(f['updated_at'],p)<=timestamp(f['fetched_at'],p),p+'.timestamps','local timestamp ordering')
    for kind,keys in [('hdb',HDB),('condo',CONDO),('landed',LANDED)]:
        if f['property_type']!=kind:require(all(f[k] is None for k in keys),p+'.'+kind,'non-applicable child fields must be null')
    if f['psf_sgd'] is not None:
        require(f['psf_basis'] in ('floor','room','land'),p+'.psf_basis','basis required')
        area=f['land_area_sqft'] if f['psf_basis']=='land' else f['area_sqft']
        require(area is not None and area>0,p+'.psf_sgd','area required')
        require(f['price_sgd'] is not None and abs(f['psf_sgd']-round(f['price_sgd']/area,2))<.00001,p+'.psf_sgd','inconsistent PSF')
    if f['property_type']=='landed':require(f['built_up_area_sqft']==f['area_sqft'],p+'.built_up_area_sqft','fixture floor area alias must agree')
    if f['tenure_type']=='freehold':require(f['lease_years'] is None,p+'.lease_years','freehold year count must be null')
    if f['last_verified_at'] is not None:require(f['extra_attributes']['verification_method']=='simulated_confirmation',p+'.last_verified_at','must not treat scraping as verification')

def validate(out=None,write_report=True):
    dest=Path(out or ROOT/'output')
    listings=read(dest/'listings.json')
    records=[json.loads(line) for line in (dest/'records54.jsonl').read_text().splitlines()]
    manifest=read(dest/'manifest.json')
    require(manifest['contract_sha256']==hashlib.sha256((ROOT/'contracts/contracts_v0.py').read_bytes()).hexdigest(),'contract','contract changed since generation')
    require(manifest['count']==len(listings)==len(records),'count','count mismatch')
    keys=[l['listing_key'] for l in listings]
    require(len(keys)==len(set(keys)),'listing_keys','duplicate key')
    ids=[r['id'] for r in records];require(len(ids)==len(set(ids)),'ids','duplicate internal ID')
    source_ids=[r['fields']['source_listing_id'] for r in records if r['fields']['source_listing_id'] is not None]
    require(len(source_ids)==len(set(source_ids)),'source_listing_id','duplicate non-null source ID')
    all_eids=[]
    for row,r in zip(listings,records):
        validate_listing(row);validate_db(r)
        require(row==to_listing(r),row['listing_key'],'DB/contract projection mismatch')
        all_eids.extend(e['evidence_id'] for e in row['evidence'])
    require(len(all_eids)==len(set(all_eids)),'evidence_ids','global collision')
    tables=read(dest/'db-tables.json');parents={r['id']:r for r in records}
    require(len(tables['listing'])==len(records),'db-tables.listing','missing DB row')
    expected_main=[dict(id=r['id'],listing_key=r['listing_key'],**{k:v for k,v in r['fields'].items() if k not in SUBFIELDS}) for r in records]
    require(tables['listing']==expected_main,'db-tables.listing','main table differs from records54')
    children=[]
    for table,kind,fields in [('hdb_detail','hdb',HDB),('condo_detail','condo',CONDO),('landed_detail','landed',LANDED)]:
        expected=[dict(listing_id=r['id'],**{k:r['fields'][k] for k in fields}) for r in records if r['fields']['property_type']==kind]
        require(tables[table]==expected,'db-tables.'+table,'wrong/missing child relation')
        children.extend(r['listing_id'] for r in tables[table])
    require(len(children)==len(set(children)),'child_tables','XOR violated')
    require(set(children)<=set(parents),'child_tables','orphan FK')
    fixture=dest/'fixtures';responses=0
    for path in sorted(fixture.glob('*.json')):
        if path.name.startswith(('search-','page-')):
            validate_search_envelope(read(path));responses+=1
    for name,t in [('context',contracts.RunContext),('profile',contracts.UserProfile),('plan',contracts.SearchPlan),('buy-profile',contracts.UserProfile),('buy-plan',contracts.SearchPlan),('snapshot',contracts.ListingSnapshot)]:check_type(t,read(fixture/f'{name}.json'),name)
    for name,p in read(fixture/'plans-by-response.json').items():
        check_type(contracts.SearchPlan,p,name)
        env=read(fixture/(name+'.json'))
        if env['data']:
            require(env['data']['plan_id']==p['plan_id'] and env['data']['profile_version']==p['profile_version'],name,'plan/profile mismatch')
            require(len(env['data']['items'])<=p['candidate_limit'],name,'candidate limit exceeded')
    bykey={r['listing_key']:r for r in listings};p=read(fixture/'profile.json');expect=read(fixture/'scenario-expectations.json')
    buckets=Counter()
    for case in expect['cases']:
        l=bykey[case['listing_key']];actual=expected_screen_bucket(l,p)
        require(actual==case['expected_screen_bucket'],case['case_id'],f'screen expected {case["expected_screen_bucket"]} got {actual}')
        buckets[actual]+=1
        if case['expected_review_codes']:
            age=(timestamp(expect['reference_time'],'reference_time')-timestamp(l['last_verified_at'],'last_verified_at')).days
            require(age>expect['verification_freshness_days'],case['case_id'],'stale case is not stale')
    require(expected_screen_bucket(read(fixture/'search-sale.json')['data']['items'][0],read(fixture/'buy-profile.json'))=='eligible','buy_profile','buy->sale mapping failed')
    pg=read(fixture/'pagination-expectations.json')
    pages=[read(fixture/f'page-{i}.json')['data']['items'] for i in [1,2]]
    got=list(dict.fromkeys(l['listing_key'] for page in pages for l in page))
    require(got==pg['expected_unique_keys'],'pagination','dedup expected keys differ')
    bad=read(fixture/'invalid-listings.json')
    for case in bad:
        try:validate_listing(case['listing'])
        except ValidationError as e:require(e.path.startswith(case['expected_error_path_prefix']),case['case_id'],str(e))
        else:raise ValidationError(case['case_id'],'invalid input was accepted')
    result=dict(passed=True,listing_count=len(listings),exact_db_field_count=len(FIELDS),
        global_evidence_count=len(all_eids),search_response_count=responses,scenario_count=len(expect['cases']),
        expected_screen_buckets=dict(buckets),invalid_inputs_rejected=len(bad),
        child_counts={k:len(v) for k,v in tables.items()},
        checks=['Supplied TypedDict keys, enums, integer amounts, complete attributes, nullable URLs, RunContext.user_id',
            '54-field sidecar, DB-to-contract projection, PK/FK/XOR, source ID and evidence ID uniqueness',
            'Evidence reference/value integrity; conflict evidence; unknown boolean not defaulted to false',
            'Timestamp ordering, PSF bases, freehold years, mock-only markers',
            'SearchResult envelopes, pagination, limits, partial/error coverage and buy-to-sale mapping',
            'Independent deterministic screening oracle for every explicit scenario; all invalid payloads rejected'],
        limitations=['No production B/C functions are implemented or executed by these checks.',
            'The synthetic evidence tests citation mechanics, not factual truth of real properties.',
            'Freshness threshold and incompatible-period rejection are documented fixture policies.'])
    if write_report:(dest/'validation-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--out',type=Path)
    print(json.dumps(validate(ap.parse_args().out),ensure_ascii=False,indent=2))
