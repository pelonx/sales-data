const RETAILER_SHEET_GID = 1421425539;
const RETAILER_GEOCODE_BATCH_SIZE = 50;
const RETAILER_GEOCODE_SLEEP_MS = 150;

function geocodeRetailerAddresses() {
  const sheet = getRetailerGeocodeSheet_();
  const values = sheet.getDataRange().getValues();
  if (values.length < 2) {
    Logger.log('No retailer rows found.');
    return;
  }

  let headers = values[0].map(String);
  const latCol = ensureRetailerGeocodeColumn_(sheet, headers, 'Latitude');
  headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0].map(String);
  const lngCol = ensureRetailerGeocodeColumn_(sheet, headers, 'Longitude');
  headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0].map(String);
  const placeIdCol = ensureRetailerGeocodeColumn_(sheet, headers, 'Google Place ID');
  headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0].map(String);
  const geocodedAtCol = ensureRetailerGeocodeColumn_(sheet, headers, 'Geocoded At');
  headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0].map(String);
  const statusCol = ensureRetailerGeocodeColumn_(sheet, headers, 'Geocode Status');

  headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0].map(String);
  const col = retailerGeocodeIndexMap_(headers);
  const rows = sheet.getRange(2, 1, Math.max(0, sheet.getLastRow() - 1), sheet.getLastColumn()).getValues();
  const geocoder = Maps.newGeocoder().setRegion('us');

  let attempted = 0;
  let success = 0;
  let skipped = 0;
  let failed = 0;

  for (let i = 0; i < rows.length; i++) {
    if (attempted >= RETAILER_GEOCODE_BATCH_SIZE) {
      break;
    }

    const row = rows[i];
    const rowNumber = i + 2;
    const existingLat = retailerGeocodeValue_(row, col, 'Latitude');
    const existingLng = retailerGeocodeValue_(row, col, 'Longitude');
    if (existingLat && existingLng) {
      skipped++;
      continue;
    }

    const address = retailerGeocodeAddress_(row, col);
    if (!address) {
      sheet.getRange(rowNumber, statusCol).setValue('Missing address');
      skipped++;
      continue;
    }

    attempted++;
    try {
      const response = geocoder.geocode(address);
      const status = response.status || 'UNKNOWN';
      if (status === 'OK' && response.results && response.results.length) {
        const result = response.results[0];
        const location = result.geometry && result.geometry.location;
        if (location && location.lat !== undefined && location.lng !== undefined) {
          sheet.getRange(rowNumber, latCol).setValue(location.lat);
          sheet.getRange(rowNumber, lngCol).setValue(location.lng);
          sheet.getRange(rowNumber, placeIdCol).setValue(result.place_id || '');
          sheet.getRange(rowNumber, geocodedAtCol).setValue(new Date());
          sheet.getRange(rowNumber, statusCol).setValue('OK');
          success++;
        } else {
          sheet.getRange(rowNumber, statusCol).setValue('No geometry');
          failed++;
        }
      } else {
        sheet.getRange(rowNumber, statusCol).setValue(status);
        failed++;
      }
    } catch (err) {
      sheet.getRange(rowNumber, statusCol).setValue(`Error: ${err.message || err}`);
      failed++;
    }

    Utilities.sleep(RETAILER_GEOCODE_SLEEP_MS);
  }

  Logger.log(`Attempted: ${attempted}; success: ${success}; failed: ${failed}; skipped: ${skipped}.`);
}

function createRetailerGeocodeTrigger() {
  deleteRetailerGeocodeTriggers();
  ScriptApp.newTrigger('geocodeRetailerAddresses')
    .timeBased()
    .everyMinutes(5)
    .create();
}

function deleteRetailerGeocodeTriggers() {
  ScriptApp.getProjectTriggers().forEach(trigger => {
    if (trigger.getHandlerFunction() === 'geocodeRetailerAddresses') {
      ScriptApp.deleteTrigger(trigger);
    }
  });
}

function getRetailerGeocodeSheet_() {
  const spreadsheet = SpreadsheetApp.getActive();
  const sheet = spreadsheet.getSheets().find(s => s.getSheetId() === RETAILER_SHEET_GID);
  if (!sheet) {
    throw new Error(`Retailer sheet not found for gid ${RETAILER_SHEET_GID}`);
  }
  return sheet;
}

function ensureRetailerGeocodeColumn_(sheet, headers, name) {
  const existing = headers.indexOf(name);
  if (existing >= 0) {
    return existing + 1;
  }
  const nextCol = headers.length + 1;
  sheet.getRange(1, nextCol).setValue(name);
  return nextCol;
}

function retailerGeocodeIndexMap_(headers) {
  const out = {};
  headers.forEach((header, i) => {
    out[String(header).trim()] = i;
  });
  return out;
}

function retailerGeocodeValue_(row, col, name) {
  const idx = col[name];
  if (idx === undefined) {
    return '';
  }
  return row[idx] === null || row[idx] === undefined ? '' : String(row[idx]).trim();
}

function retailerGeocodeAddress_(row, col) {
  const address = retailerGeocodeValue_(row, col, 'Address');
  if (!address) {
    return '';
  }
  if (/\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b/.test(address)) {
    return address;
  }
  const city = retailerGeocodeValue_(row, col, 'City');
  return [address, city, 'WA'].filter(Boolean).join(', ');
}
