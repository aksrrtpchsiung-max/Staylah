"""Reduce existing observations to non-personal empirical prototype facts."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT.parent/'can-rent-lah'/'record'/'propertyguru-test'

def prepare():
    profiles=[]
    old=[('hdb','CLEMENTI'),('condo','CLEMENTI'),('room','CLEMENTI'),('landed','CLEMENTI')]
    for name,area in old:
        x=json.loads((AUDIT/f'field-audit-{name}.json').read_text())
        ld=x['listingDetail']
        profiles.append(dict(area=area,source_url=x.get('url') or x['listingData']['url'],
            source_id=ld['id'],unitDetails=ld['unitDetails'],price=ld['price'],
            project=ld['project']['metaByType'][ld['project']['type']],
            location=ld['location'],description_signals={'owner_explicitly_stays':name=='room'}))
    live=json.loads((ROOT/'reference/live-observations.json').read_text())
    for x in live['details']:
        profiles.append(dict(x,area=x['sample_area'].upper().replace(' ','_')))
    result=[]
    for i,x in enumerate(profiles,1):
        u=x['unitDetails'];p=x['project'];dims=u['dimensions']
        code=p['property']['type']['code']
        property_type={'H':'hdb','N':'condo','L':'landed'}.get(code,'other')
        scope='room' if u['rentalType']['code']=='ROOM' else 'whole_unit'
        size=dims['room' if scope=='room' else 'floor']['size']
        land=dims['land']['size']
        def num(entries):
            if not entries:return None
            v=entries[0]['value']
            return v.get('value') if isinstance(v,dict) else v
        rules={k:u.get(v) for k,v in {'owner_stays':'ownerStays','utilities_included':'utilitiesIncluded',
            'wifi_included':'wifiIncluded','visitors_allowed':'visitorsAllowed','pets_allowed':'petFriendly'}.items()}
        cooking=(u.get('cookingType') or {}).get('code')
        rules['cooking_policy']={'NOT':'none','LIGHT':'light','ALL':'full','YES':'full'}.get(cooking,'unknown')
        rules['furnishing']={'FULL':'fully','PART':'partially','UNFUR':'unfurnished'}.get((u.get('furnishing') or {}).get('code'),'unknown')
        result.append(dict(prototype_id=f'P{i:02}',source_id=x['source_id'],source_url=x['source_url'],
            area=x['area'],property_type=property_type,scope=scope,amount=x['price']['min'],
            area_sqft=num(size),land_area_sqft=num(land),
            bedrooms=u['configuration']['bedrooms']['value'],bedrooms_text=u['configuration']['bedrooms']['text'],bathrooms=u['configuration']['bathrooms']['value'],
            room_type={'COM':'common','MAS':'master'}.get((u.get('roomType') or {}).get('code'),'unknown'),
            rules=rules,tenure_type='freehold' if p['tenure']['code']=='F' else 'leasehold',
            lease_years=99 if p['tenure']['code']=='L99' else None,
            top_year=p.get('completionYear'),floor_level=(u.get('floorLevel') or {}).get('code'),
            hdb_model=(u.get('hdbType') or {}).get('description'),
            landed_type=p['property']['subType']['text'] if property_type=='landed' else None,
            facilities=[f['description'] for f in p.get('facilities',[])],
            center=x['location']['point'],description_signals=x.get('description_signals',{})))
    stats={}
    for scope in ['room','whole_unit']:
        group=[x for x in result if x['scope']==scope]
        stats[scope]={'n':len(group),'missing_rules':{k:sum(x['rules'][k] in (None,'unknown') for x in group)
            for k in result[0]['rules']}}
    output=dict(note='Small convenience sample, not market distribution; generated data is entirely synthetic.',
        search_rows_observed=sum(len(p['rows']) for p in live['search_pages']),details_observed=len(result),
        sample_date='2026-09-14',profiles=result,statistics=stats)
    (ROOT/'reference/observed-profiles.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(stats,ensure_ascii=False,indent=2))

if __name__=='__main__':prepare()
