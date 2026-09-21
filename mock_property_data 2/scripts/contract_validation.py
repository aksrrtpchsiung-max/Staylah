"""Runtime checks derived from the supplied TypedDict contract (stdlib only).

The supplied file is not a Pydantic model. Structural validation here is strict,
including rejecting bool as int. Semantic conventions are explicit below.
"""
import importlib.util
import types
import typing
from datetime import datetime
from functools import lru_cache
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('mock_contracts_v0',ROOT/'contracts/contracts_v0.py')
contracts=importlib.util.module_from_spec(spec)
spec.loader.exec_module(contracts)

class ValidationError(ValueError):
    def __init__(self,path,message):
        self.path=path
        super().__init__(f'{path}: {message}')

def require(condition,path,message):
    if not condition:raise ValidationError(path,message)

@lru_cache(None)
def hints(cls):return typing.get_type_hints(cls,vars(contracts),vars(contracts))

def check_type(tp,value,path='value',bindings=None):
    bindings=bindings or {}
    if isinstance(tp,typing.TypeVar):return check_type(bindings[tp],value,path,bindings)
    if isinstance(tp,typing.ForwardRef):return check_type(eval(tp.__forward_arg__,vars(contracts)),value,path,bindings)
    origin,args=typing.get_origin(tp),typing.get_args(tp)
    if origin in (typing.Union,types.UnionType):
        for candidate in args:
            try:check_type(candidate,value,path,bindings);return
            except ValidationError:pass
        raise ValidationError(path,f'value does not match {tp}')
    if origin is typing.Literal:
        require(any(type(value) is type(x) and value==x for x in args),path,f'expected {args}')
    elif origin is list:
        require(type(value) is list,path,'expected array')
        for i,v in enumerate(value):check_type(args[0],v,f'{path}[{i}]',bindings)
    elif origin is dict:
        require(type(value) is dict,path,'expected object')
        for k,v in value.items():
            check_type(args[0],k,path+'.key',bindings)
            check_type(args[1],v,path+'.'+str(k),bindings)
    elif typing.is_typeddict(origin or tp):
        cls=origin or tp
        require(type(value) is dict,path,'expected object')
        local=dict(bindings)
        if origin:local.update(zip(cls.__parameters__,args))
        fields=hints(cls)
        require(set(value)==set(fields),path,'keys differ: '+str(sorted(set(value)^set(fields))))
        for key,field_type in fields.items():check_type(field_type,value[key],path+'.'+key,local)
    else:
        require(type(value) is tp or (tp is float and type(value) is int),path,f'expected {tp}')

def timestamp(s,path):
    try:d=datetime.fromisoformat(s)
    except (TypeError,ValueError):raise ValidationError(path,'expected ISO timestamp')
    require(d.tzinfo is not None,path,'timezone required')
    return d

def get_path(obj,path):
    for part in path.split('.'):
        if not isinstance(obj,dict) or part not in obj:raise ValidationError(path,'unknown evidence field')
        obj=obj[part]
    return obj

def validate_listing(row,path='listing'):
    check_type(contracts.Listing,row,path)
    require(row['source_mode']=='mock',path+'.source_mode','this package contains mock only')
    require(bool(row['listing_key']),path+'.listing_key','stable nonempty key required')
    p=row['price'];a=row['attributes']
    require(p['amount'] is None or p['amount']>0,path+'.price.amount','positive amount or null required')
    if p['status']=='known':
        require(p['amount'] is not None and p['period'] is not None,path+'.price','known requires amount and period')
    else:
        require(p['amount'] is None,path+'.price.amount','unknown/conflict has no selected amount in these fixtures')
    if row['transaction_type']=='sale' and p['status']=='known':
        require(p['period']=='total',path+'.price.period','sale uses total')
    for field,v in [('bedrooms',row['bedrooms']),('attributes.bathrooms',a['bathrooms'])]:
        require(v is None or v>=0,path+'.'+field,'nonnegative count required')
    require(a['area_sqft'] is None or a['area_sqft']>0,path+'.attributes.area_sqft','positive area or null')
    require(a['lease_years'] is None or a['lease_years']>0,path+'.attributes.lease_years','positive years or null')
    if a['tenure_type']=='freehold':require(a['lease_years'] is None,path+'.attributes.lease_years','freehold is not a finite lease')
    if a['listing_scope']=='whole_unit':require(a['room_type']=='unknown',path+'.attributes.room_type','room type is not applicable to whole units')
    fetched=timestamp(row['fetched_at'],path+'.fetched_at')
    for field in ['source_updated_at','last_verified_at']:
        if row[field]:require(timestamp(row[field],path+'.'+field)<=fetched,path+'.'+field,'cannot be after fetched_at')
    if row['listed_date']:
        from datetime import date
        try:d=date.fromisoformat(row['listed_date'])
        except ValueError:raise ValidationError(path+'.listed_date','ISO date required')
        require(d<=fetched.date(),path+'.listed_date','cannot be a future published date')
    evidence={}
    for i,e in enumerate(row['evidence']):
        ep=f'{path}.evidence[{i}]'
        require(e['evidence_id'] not in evidence,ep+'.evidence_id','duplicate evidence ID')
        require(bool(e['excerpt'].strip()),ep+'.excerpt','nonempty excerpt required')
        require(timestamp(e['observed_at'],ep+'.observed_at')<=fetched,ep+'.observed_at','future evidence')
        target=get_path(row,e['field'])
        # Only explicitly annotated conflict fields may disagree with the selected value.
        conflict=e['field'] in row['field_issues'] and (target is None or target=='unknown')
        require(e['value']==target or conflict,ep+'.value','evidence disagrees with normalized field')
        evidence[e['evidence_id']]=e
    for eid in p['evidence_ids']:
        require(eid in evidence,path+'.price.evidence_ids','dangling evidence reference')
        require(evidence[eid]['field'].startswith('price.'),path+'.price.evidence_ids','must reference price evidence')
    if p['status']=='known':require(any(evidence[e]['field']=='price.amount' for e in p['evidence_ids']),path+'.price.evidence_ids','amount evidence required')
    if p['status']=='conflict':
        amounts={evidence[e]['value'] for e in p['evidence_ids'] if evidence[e]['field']=='price.amount'}
        require(len(amounts)>=2,path+'.price.evidence_ids','conflict requires at least two distinct observed amounts')
    for field in row['field_issues']:get_path(row,field)
    # Every nonempty fixture fact used by screening has structured Evidence.
    facts={'transaction_type':row['transaction_type'],'bedrooms':row['bedrooms'],
           'location_id':row['location_id'],'listing_status':row['listing_status']}
    facts.update({'attributes.'+k:v for k,v in a.items()})
    for field,v in facts.items():
        if v is not None and v!='unknown':require(any(e['field']==field and e['value']==v for e in evidence.values()),path+'.evidence','missing fact evidence for '+field)

def validate_search_envelope(result):
    check_type(contracts.Result[contracts.SearchResult],result,'result')
    status=result['status']
    require((result['data'] is None)==(status=='error'),'result.data','error must have null data; others must have data')
    require(status=='success' or bool(result['issues']),'result.issues','partial/error requires issue')
    require(result['meta']['duration_ms']>=0,'result.meta.duration_ms','nonnegative duration')
    if result['data'] is None:return
    data=result['data'];c=data['coverage']
    keys=[r['listing_key'] for r in data['items']]
    require(len(keys)==len(set(keys)),'result.data.items','duplicate listing_key in one response')
    require(not c['next_pages'] or c['has_more'],'result.data.coverage','next_pages requires has_more')
    require(set(c['failed_sources'])<=set(c['queried_sources']),'result.data.coverage.failed_sources','must have been queried')
    require(not(c['queries_completed'] and c['failed_sources']),'result.data.coverage','failed source cannot mean all queries completed')
    for i,row in enumerate(data['items']):validate_listing(row,f'result.data.items[{i}]')
