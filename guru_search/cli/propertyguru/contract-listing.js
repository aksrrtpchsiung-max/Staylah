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
  const match = raw.match(/(?:S\$|SGD\s*)?(\d[\d,]*)(?:\.0+)?/i);
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
  return 'other';
}

function normalizeScope(raw) {
  const value = text(raw)?.toLowerCase();
  if (!value) return null;
  if (/bed.?space|shared.?room/.test(value)) return 'bedspace';
  if (/master.?room|common.?room|\broom\b/.test(value)) return 'room';
  if (/whole.?unit|entire.?unit|full.?unit/.test(value)) return 'whole_unit';
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
  return fallback === 'sale' ? 'sale' : 'rent';
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

  const sourceUrl = text(raw.url) || `${baseUrl}/listing/${sourceId}`;
  const listingKey = `${SOURCE}:${sourceId}`;
  const evidence = [];
  const fieldIssues = [];
  const addEvidence = (field, value, sourceField) => {
    if (value === null || value === undefined || value === '') return [];
    const evidenceId = `${listingKey}:${field}`;
    evidence.push({
      evidence_id: evidenceId,
      field,
      value,
      source_url: sourceUrl,
      observed_at: fetchedAt,
      excerpt: sourceExcerpt(sourceField, value),
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
  const areaSqft = integer(firstValue(raw, ['floorArea', 'area.value', 'floorArea.value']));
  const propertyType = normalizePropertyType(featureText || raw.propertyType);
  const listingScope = normalizeScope(firstValue(raw, ['listingScope', 'rentalScope', 'unitType']) || classificationText);
  const furnishingRaw = firstValue(raw, ['furnishing', 'furnishingType', 'listingFeatures.furnishing']);
  const tenureRaw = firstValue(raw, ['tenure', 'tenureType', 'propertyTenure']);
  const leaseYears = integer(firstValue(raw, ['leaseYears', 'tenureYears']));
  const listedDateRaw = firstValue(raw, ['postedOn.date', 'postedOn.iso', 'postedOn.value', 'listedDate']);
  const listedDate = normalizedDate(listedDateRaw);
  if (listedDateRaw !== null && listedDate === null) fieldIssues.push('listed_date:unparseable');
  const sourceUpdatedRaw = firstValue(raw, ['updatedAt', 'sourceUpdatedAt', 'lastUpdatedAt']);
  const sourceUpdatedAt = text(sourceUpdatedRaw)?.match(/^\d{4}-\d{2}-\d{2}/) ? text(sourceUpdatedRaw) : null;

  const priceEvidence = amount === null ? [] : addEvidence('price.amount', amount, 'price.value');
  const currencyEvidence = addEvidence('price.currency', DEFAULT_CURRENCY, 'PropertyGuru Singapore marketplace');
  const period = transactionType === 'rent' ? 'month' : 'total';
  addEvidence('transaction_type', transactionType, 'typeCode');
  addEvidence('title', title, 'localizedTitle');
  if (propertyType !== 'unknown') addEvidence('attributes.property_type', propertyType, 'listingFeatures');
  if (listingScope !== null) addEvidence('attributes.listing_scope', listingScope, 'listing scope/title');
  if (bedrooms !== null) addEvidence('bedrooms', bedrooms, 'bedrooms');
  if (bathrooms !== null) addEvidence('attributes.bathrooms', bathrooms, 'bathrooms');
  if (areaSqft !== null) addEvidence('attributes.area_sqft', areaSqft, 'floorArea');
  const furnishing = normalizeFurnishing(furnishingRaw);
  if (furnishing !== 'unknown') addEvidence('attributes.furnishing', furnishing, 'furnishing');
  const tenureType = normalizeTenure(tenureRaw);
  if (tenureType !== 'unknown') addEvidence('attributes.tenure_type', tenureType, 'tenure');
  if (leaseYears !== null) addEvidence('attributes.lease_years', leaseYears, 'leaseYears');
  if (listedDate !== null) addEvidence('listed_date', listedDate, 'postedOn');
  if (sourceUpdatedAt !== null) addEvidence('source_updated_at', sourceUpdatedAt, 'updatedAt');
  const periodEvidence = addEvidence('price.period', period, 'requested listing type');

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
      currency: DEFAULT_CURRENCY,
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
      ensuite_bathroom: yesNo(firstValue(raw, ['ensuiteBathroom', 'hasEnsuiteBathroom'])),
      owner_stays: yesNo(firstValue(raw, ['ownerStays', 'landlordStays'])),
      cooking_policy: 'unknown',
      utilities_included: yesNo(firstValue(raw, ['utilitiesIncluded'])),
      wifi_included: yesNo(firstValue(raw, ['wifiIncluded'])),
      visitors_allowed: yesNo(firstValue(raw, ['visitorsAllowed', 'guestsAllowed'])),
      pets_allowed: yesNo(firstValue(raw, ['petsAllowed'])),
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
