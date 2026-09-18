const SOURCE = 'propertyguru';
const DEFAULT_CURRENCY = 'SGD';

function text(value) {
  if (value === null || value === undefined) return null;
  const normalized = String(value).trim();
  return normalized || null;
}

function valueAt(object, path) {
  return path.split('.').reduce((current, key) => current?.[key], object);
}

function firstValue(object, paths) {
  for (const path of paths) {
    const value = valueAt(object, path);
    if (value !== null && value !== undefined && value !== '') return value;
  }
  return null;
}

function integer(value) {
  if (typeof value === 'number' && Number.isInteger(value) && value >= 0) return value;
  const raw = text(value);
  if (!raw) return null;
  const normalized = raw.replace(/,/g, '');
  if (!/^\d+(?:\.0+)?(?:\s*(?:sq\.?\s*ft|sqft))?$/i.test(normalized)) return null;
  const parsed = Number.parseFloat(normalized);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

function priceAmount(value) {
  const direct = integer(value);
  if (direct !== null) return direct;
  const raw = text(value);
  if (!raw) return null;
  // 不把范围、每平方英尺单价或小数截断成一个确定的挂牌金额。
  const match = raw.match(/^(?:S\$|SGD\s*|\$)?\s*(\d[\d,]*)(?:\.0+)?(?:\s*(?:\/\s*(?:mo(?:nth)?|week|wk)|per\s+(?:month|week)|pcm|pw))?$/i);
  return match ? integer(match[1]) : null;
}

function yesNo(value) {
  if (typeof value === 'boolean') return value;
  const raw = text(value)?.toLowerCase();
  if (['yes', 'true', 'allowed', 'included'].includes(raw)) return true;
  if (['no', 'false', 'not allowed', 'not included'].includes(raw)) return false;
  return null;
}

function normalizedDate(value) {
  const raw = text(value);
  if (!raw) return null;
  const match = raw.match(/^(\d{4}-\d{2}-\d{2})(?:[tT ][\d:.+-]+)?$/);
  return match ? match[1] : null;
}

function normalizePropertyType(raw) {
  const value = text(raw)?.toLowerCase();
  if (!value) return 'unknown';
  if (/\bhdb\b|housing.?development/.test(value)) return 'hdb';
  if (/condo|minium|executive.?condo/.test(value)) return 'condo';
  if (/landed|bungalow|terraced|semi.?detached|detached/.test(value)) return 'landed';
  if (/apartment|walk.?up/.test(value)) return 'apartment';
  return value === 'other' ? 'other' : 'unknown';
}

function normalizeScope(raw) {
  const value = text(raw)?.toLowerCase();
  if (!value) return null;
  if (/bed.?space|shared.?room/.test(value)) return 'bedspace';
  if (/whole.?unit|entire.?unit|full.?unit/.test(value)) return 'whole_unit';
  if (/master.?room|common.?room|room\s+(?:rental|for rent)|room.?only|^room$/.test(value)) return 'room';
  return null;
}

function normalizeRoomType(raw) {
  const value = text(raw)?.toLowerCase();
  if (/master/.test(value || '')) return 'master';
  if (/common/.test(value || '')) return 'common';
  if (/shared|bed.?space/.test(value || '')) return 'shared';
  return 'unknown';
}

function normalizeFurnishing(raw) {
  const value = text(raw)?.toLowerCase();
  if (/fully/.test(value || '')) return 'fully';
  if (/partial/.test(value || '')) return 'partially';
  if (/unfurnished/.test(value || '')) return 'unfurnished';
  return 'unknown';
}

function normalizeTenure(raw) {
  const value = text(raw)?.toLowerCase();
  if (/freehold/.test(value || '')) return 'freehold';
  if (/leasehold/.test(value || '')) return 'leasehold';
  return 'unknown';
}

function normalizeTransactionType(raw, fallback) {
  const value = text(raw)?.toLowerCase();
  if (/sale|buy/.test(value || '')) return 'sale';
  if (/rent/.test(value || '')) return 'rent';
  return fallback === 'sale' ? 'sale' : 'rent';
}

function normalizePricePeriod(raw) {
  const value = text(raw)?.toLowerCase() || '';
  if (/\b(?:week|weekly|wk|pw)\b/.test(value)) return 'week';
  if (/\b(?:month|monthly|mo|pcm)\b/.test(value)) return 'month';
  if (value === 'total') return 'total';
  return null;
}

function sourceExcerpt(label, rawValue) {
  const value = typeof rawValue === 'string' ? rawValue.trim() : JSON.stringify(rawValue);
  return `PropertyGuru search result: ${label}=${value}`;
}

/**
 * Convert one PropertyGuru search-card record into Falcon's complete Listing
 * contract. Every contract key is returned. Unknown facts stay null/unknown;
 * callers can later merge detail-page evidence for shortlisted listings.
 */
export function buildSearchListing(raw, { baseUrl, requestedListingType, fetchedAt = new Date().toISOString() }) {
  const sourceId = text(raw?.id);
  if (!sourceId) return null;

  const sourceUrl = new URL(text(raw.url) || `/listing/${sourceId}`, baseUrl).href;
  const listingKey = `${SOURCE}:${sourceId}`;
  const evidence = [];
  const fieldIssues = [];
  const addEvidence = (field, value, sourceField, original = value) => {
    if (value === null || value === undefined || value === '') return [];
    const evidenceId = `${listingKey}:${field}`;
    evidence.push({
      evidence_id: evidenceId,
      field,
      value,
      source_url: sourceUrl,
      observed_at: fetchedAt,
      excerpt: sourceExcerpt(sourceField, original),
    });
    return [evidenceId];
  };

  const title = text(raw.localizedTitle) || '';
  const transactionType = normalizeTransactionType(raw.typeCode, requestedListingType);
  const priceRaw = firstValue(raw, ['price.value', 'priceValue', 'price.pretty', 'priceDisplay']);
  const amount = priceAmount(priceRaw);
  if (priceRaw !== null && amount === null) fieldIssues.push('price.amount:unparseable');

  const featureText = Array.isArray(raw.listingFeatures)
    ? raw.listingFeatures.map((feature) => text(feature?.text)).filter(Boolean).join(' | ')
    : '';
  const classificationText = [
    featureText,
    raw.propertyType,
    raw.localizedTitle,
    raw.fullAddress,
    firstValue(raw, ['listingScope', 'rentalScope', 'unitType']),
  ].filter(Boolean).join(' | ');

  const bedrooms = integer(raw.bedrooms);
  const bathrooms = integer(raw.bathrooms);
  const areaRaw = firstValue(raw, ['floorArea.value', 'floorArea', 'area.value']);
  const areaSqft = integer(areaRaw);
  const propertyType = normalizePropertyType(raw.propertyType || featureText);
  const listingScope = normalizeScope(firstValue(raw, ['listingScope', 'rentalScope', 'unitType']) || classificationText);
  const furnishingRaw = firstValue(raw, ['furnishing', 'furnishingType', 'listingFeatures.furnishing']);
  const tenureRaw = firstValue(raw, ['tenure', 'tenureType', 'propertyTenure']);
  const leaseYears = integer(firstValue(raw, ['leaseYears', 'tenureYears']));
  const listedDateRaw = firstValue(raw, ['postedOn.date', 'postedOn.iso', 'postedOn.value', 'listedDate']);
  const listedDate = normalizedDate(listedDateRaw);
  if (listedDateRaw !== null && listedDate === null) fieldIssues.push('listed_date:unparseable');
  const sourceUpdatedRaw = firstValue(raw, ['updatedAt', 'sourceUpdatedAt', 'lastUpdatedAt']);
  const updatedText = text(sourceUpdatedRaw);
  const sourceUpdatedAt = updatedText && /(?:Z|[+-]\d{2}:\d{2})$/i.test(updatedText)
    && Number.isFinite(Date.parse(updatedText)) ? new Date(updatedText).toISOString() : null;
  if (sourceUpdatedRaw !== null && sourceUpdatedAt === null) fieldIssues.push('source_updated_at:unparseable');

  const priceEvidence = amount === null ? [] : addEvidence('price.amount', amount, 'price', priceRaw);
  const sourceCurrency = text(firstValue(raw, ['price.currency', 'currency']));
  const currency = sourceCurrency || DEFAULT_CURRENCY;
  const currencyEvidence = addEvidence('price.currency', currency, sourceCurrency ? 'price currency' : 'PropertyGuru Singapore marketplace');
  const periodRaw = firstValue(raw, ['price.period', 'price.frequency', 'price.pretty', 'priceDisplay']);
  const period = normalizePricePeriod(periodRaw) || (transactionType === 'sale' ? 'total' : null);
  if (period === null) fieldIssues.push('price.period:unknown');
  addEvidence('transaction_type', transactionType, raw.typeCode ? 'typeCode' : 'search page transaction type', raw.typeCode || requestedListingType);
  addEvidence('title', title, 'localizedTitle');
  if (propertyType !== 'unknown') addEvidence('attributes.property_type', propertyType, 'propertyType/listingFeatures', raw.propertyType || featureText);
  if (listingScope !== null) addEvidence('attributes.listing_scope', listingScope, 'listing scope/title', classificationText);
  if (bedrooms !== null) addEvidence('bedrooms', bedrooms, 'bedrooms');
  if (bathrooms !== null) addEvidence('attributes.bathrooms', bathrooms, 'bathrooms');
  if (areaSqft !== null) addEvidence('attributes.area_sqft', areaSqft, 'floorArea', areaRaw);
  const furnishing = normalizeFurnishing(furnishingRaw);
  if (furnishing !== 'unknown') addEvidence('attributes.furnishing', furnishing, 'furnishing');
  const tenureType = normalizeTenure(tenureRaw);
  if (tenureType !== 'unknown') addEvidence('attributes.tenure_type', tenureType, 'tenure');
  if (leaseYears !== null) addEvidence('attributes.lease_years', leaseYears, 'leaseYears');
  if (listedDate !== null) addEvidence('listed_date', listedDate, 'postedOn');
  if (sourceUpdatedAt !== null) addEvidence('source_updated_at', sourceUpdatedAt, 'updatedAt');
  const periodEvidence = addEvidence('price.period', period, periodRaw ? 'price label' : 'sale page', periodRaw || requestedListingType);

  const flagFields = {
    ensuite_bathroom: ['ensuiteBathroom', 'hasEnsuiteBathroom'], owner_stays: ['ownerStays', 'landlordStays'],
    utilities_included: ['utilitiesIncluded'], wifi_included: ['wifiIncluded'],
    visitors_allowed: ['visitorsAllowed', 'guestsAllowed'], pets_allowed: ['petsAllowed'],
  };
  const flags = Object.fromEntries(Object.entries(flagFields).map(([key, paths]) => {
    const original = firstValue(raw, paths);
    const value = yesNo(original);
    if (value !== null) addEvidence(`attributes.${key}`, value, paths.join('/'), original);
    return [key, value];
  }));

  return {
    listing_key: listingKey,
    source: SOURCE,
    source_listing_id: sourceId,
    source_url: sourceUrl,
    source_mode: 'live',
    title,
    transaction_type: transactionType,
    price: {
      amount,
      currency,
      period,
      status: amount === null ? 'unknown' : 'known',
      evidence_ids: [...priceEvidence, ...currencyEvidence, ...periodEvidence],
    },
    attributes: {
      property_type: propertyType,
      unit_layout: /studio/i.test(classificationText) ? 'studio' : null,
      listing_scope: listingScope,
      area_sqft: areaSqft,
      bathrooms,
      room_type: normalizeRoomType(classificationText),
      ...flags,
      cooking_policy: 'unknown',
      furnishing,
      tenure_type: tenureType,
      lease_years: leaseYears,
    },
    bedrooms,
    location_id: null,
    listing_status: 'unknown',
    listed_date: listedDate,
    fetched_at: fetchedAt,
    source_updated_at: sourceUpdatedAt,
    last_verified_at: null,
    raw_description: null,
    raw_details: [],
    evidence,
    field_issues: fieldIssues,
  };
}

/** Sparse, observed detail facts. No request filters are used as listing facts. */
export function buildListingDetail(raw, { sourceId, sourceUrl, fetchedAt = new Date().toISOString() }) {
  const stripHtml = value => String(value || '').replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, '').trim();
  const details = Array.isArray(raw.detailItems) ? raw.detailItems : [];
  const facts = [];
  const add = (field, value, original) => {
    if (value === null || value === undefined || value === '' || value === 'unknown') return;
    facts.push({ field, value, excerpt: `PropertyGuru detail: ${original}` });
  };
  const mapping = {
    bedrooms: ['bedrooms', integer], bedroom: ['bedrooms', integer], beds: ['bedrooms', integer],
    bathrooms: ['attributes.bathrooms', integer], bathroom: ['attributes.bathrooms', integer],
    floorarea: ['attributes.area_sqft', integer], sqft: ['attributes.area_sqft', integer],
    furnishing: ['attributes.furnishing', normalizeFurnishing],
    propertytype: ['attributes.property_type', normalizePropertyType],
    listingscope: ['attributes.listing_scope', normalizeScope], rentalscope: ['attributes.listing_scope', normalizeScope],
    roomtype: ['attributes.room_type', normalizeRoomType],
    tenure: ['attributes.tenure_type', normalizeTenure], leaseyears: ['attributes.lease_years', integer],
  };
  // Only explicit structured labels establish these facts; prose remains in raw_description.
  Object.assign(mapping, {
    ensuitebathroom: ['attributes.ensuite_bathroom', yesNo], ownerstays: ['attributes.owner_stays', yesNo],
    utilitiesincluded: ['attributes.utilities_included', yesNo], wifiincluded: ['attributes.wifi_included', yesNo],
    visitorsallowed: ['attributes.visitors_allowed', yesNo], petsallowed: ['attributes.pets_allowed', yesNo],
    listeddate: ['listed_date', normalizedDate],
  });
  const addPrice = value => {
    const original = typeof value === 'object' ? JSON.stringify(value) : String(value);
    const amountRaw = typeof value === 'object' ? firstValue(value, ['value', 'pretty', 'amount']) : value;
    add('price.amount', priceAmount(amountRaw), `price=${original}`);
    const periodRaw = typeof value === 'object' ? firstValue(value, ['period', 'frequency', 'pretty']) : value;
    add('price.period', normalizePricePeriod(periodRaw), `price=${original}`);
    const currency = typeof value === 'object' ? text(value.currency) : /S\$|SGD/i.test(original) ? 'SGD' : null;
    if (currency) add('price.currency', currency, `price=${original}`);
  };
  const info = raw.propertyInfo && !Array.isArray(raw.propertyInfo) ? raw.propertyInfo : {};
  for (const entry of [...Object.entries(info).map(([label, value]) => ({ label, value })), ...details]) {
    const label = String(entry.label || '').toLowerCase().replace(/[^a-z]/g, '');
    if (['price', 'askingprice', 'monthlyrent', 'rent'].includes(label)) {
      if (entry.value !== null && entry.value !== undefined) addPrice(entry.value);
      if (label === 'monthlyrent') add('price.period', 'month', `${entry.label}=${String(entry.value)}`);
      continue;
    }
    const mapper = mapping[label];
    if (mapper) add(mapper[0], mapper[1](entry.value), `${entry.label}=${String(entry.value)}`);
  }
  if (raw.subtitle) add('title', stripHtml(raw.subtitle), stripHtml(raw.subtitle));
  const rawDetails = details.map(entry => `${entry.label || ''}: ${String(entry.value ?? '')}`);
  for (const [key, label] of [['amenityList', 'Amenity'], ['facilityList', 'Facility'], ['nearbyMrt', 'Listing mentions nearby MRT']]) {
    for (const value of raw[key] || []) rawDetails.push(`${label}: ${String(value)}`);
  }
  if (raw.locationInfo) rawDetails.push(`Location information: ${JSON.stringify(raw.locationInfo)}`);
  return {
    source_listing_id: String(sourceId), source_url: sourceUrl, fetched_at: fetchedAt,
    raw_description: stripHtml(raw.description) || null, raw_details: rawDetails, facts,
  };
}
