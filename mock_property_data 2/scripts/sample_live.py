"""Small read-only sample; never needed to regenerate the offline mock dataset."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSION = 'pg-mock-sampling'

def cli(*args):
    p = subprocess.run(['opencli', 'browser', SESSION, *args], text=True,
                       capture_output=True, check=True, timeout=45)
    if args[0] == 'close':
        return p.stdout.strip()
    return json.loads(p.stdout)

SEARCH_JS = """(() => {
const rows=Object.values(window.__NEXT_DATA__.props.pageProps.pageData.data.listingsData);
return rows.filter(e=>e.listingData).map(e=>{const d=e.listingData;return {
 id:d.id,url:d.url,title:d.localizedTitle,price:d.price,
 bedrooms:d.bedrooms,bathrooms:d.bathrooms,floorArea:d.floorArea,
 availability:d.availabilityInfo,features:d.listingFeatures,
 propertyType:d.listingFeatures?.find(f=>f?.dataAutomationId==='listing-card-v2-unit-type')?.text,
 scope:d.listingFeatures?.some(f=>f?.dataAutomationId==='listing-card-v2-room-type')?'room':'whole_unit'
};});})()"""

DETAIL_JS = """(() => {
const d=window.__NEXT_DATA__?.props?.pageProps?.pageData?.data;
if(!d?.listingDetail)return {error:'Missing listingDetail'};
const l=d.listingDetail,p=l.project?.metaByType?.[l.project?.type];
return {source_url:location.href,source_id:l.id,unitDetails:l.unitDetails,
price:l.price,dates:l.dates,location:l.location,status:l.statusCode,
propertyType:d.listingData?.propertyType,project:p,
details:d.detailsData?.metatable?.items,amenities:d.amenitiesData?.data,
facilities:d.facilitiesData?.data,
description:d.descriptionBlockData?.description};})()"""

if __name__ == '__main__':
    pages, details = [], []
    try:
        for area in ['tampines', 'jurong east']:
            from urllib.parse import urlencode
            url='https://www.propertyguru.com.sg/property-for-rent?'+urlencode(dict(freetext=area,market='residential'))
            cli('open', url, '--window', 'background')
            rows=cli('eval', SEARCH_JS)
            pages.append(dict(area=area,rows=rows))
            selected, seen = [], set()
            for row in rows:
                kind=(row.get('propertyType'),row['scope'])
                if kind not in seen:
                    selected.append(row);seen.add(kind)
                if len(selected)==3:break
            for row in selected:
                cli('open',row['url'],'--window','background')
                obj=cli('eval',DETAIL_JS)
                if 'error' in obj:raise RuntimeError(obj)
                # Do not redistribute full listing prose or business contact details.
                desc=obj.pop('description',None) or ''
                obj['description_signals']={word:word in desc.lower() for word in
                    ['no cooking','light cooking','no owner','landlord','wifi','utilities','pets','visitors','shared bath']}
                obj['sample_area']=area
                details.append(obj)
                print(area,row['propertyType'],row['scope'],row['id'],flush=True)
        (ROOT/'reference'/'live-observations.json').write_text(json.dumps(
            dict(sampled_on='2026-09-14',search_pages=pages,details=details),ensure_ascii=False,indent=2)+'\n')
    finally:
        cli('close')
