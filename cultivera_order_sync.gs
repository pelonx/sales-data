const CULTIVERA_SPREADSHEET_ID = '1kY5e6SXd7eQ7GJx-jg6M1R60WCCZ9I_25Eb7ZmuDKHw';
const CULTIVERA_DATA_SHEET_NAME = 'Cultivera Data';
const CULTIVERA_SYNC_LOG_SHEET_NAME = 'Cultivera Sync Log';
const CULTIVERA_EXPORT_URL = 'https://api-wa.cultiverapro.com/api/v1/Orders/export-order-details-to-excel';

const CULTIVERA_PROP_TOKEN = 'CULTIVERA_BEARER_TOKEN';
const CULTIVERA_PROP_PAYLOAD = 'CULTIVERA_EXPORT_PAYLOAD_JSON';
const CULTIVERA_PROP_TZO = 'CULTIVERA_TZO_MINUTES';

function checkCultiveraSyncSetup() {
  const props = PropertiesService.getScriptProperties();
  const spreadsheet = SpreadsheetApp.openById(CULTIVERA_SPREADSHEET_ID);
  const sheet = getOrCreateCultiveraSheet_(CULTIVERA_DATA_SHEET_NAME);
  Logger.log(`Spreadsheet: ${spreadsheet.getName()}`);
  Logger.log(`Target sheet: ${sheet.getName()}; rows: ${sheet.getLastRow()}; columns: ${sheet.getLastColumn()}`);
  Logger.log(`${CULTIVERA_PROP_TOKEN} configured: ${Boolean(props.getProperty(CULTIVERA_PROP_TOKEN))}`);
  Logger.log(`${CULTIVERA_PROP_PAYLOAD} configured: ${Boolean(props.getProperty(CULTIVERA_PROP_PAYLOAD))}`);
  Logger.log(`${CULTIVERA_PROP_TZO}: ${props.getProperty(CULTIVERA_PROP_TZO) || '-420 default'}`);
}

function testCultiveraExportRequest() {
  const response = fetchCultiveraExport_();
  const headers = response.getAllHeaders();
  Logger.log(`HTTP status: ${response.getResponseCode()}`);
  Logger.log(`Content-Type: ${headerValue_(headers, 'Content-Type') || headerValue_(headers, 'content-type') || ''}`);
  Logger.log(`Content-Disposition: ${headerValue_(headers, 'Content-Disposition') || headerValue_(headers, 'content-disposition') || ''}`);
  Logger.log(`Body bytes: ${response.getBlob().getBytes().length}`);
  Logger.log(`Body preview: ${safeTextPreview_(response, 300)}`);
}

function syncCultiveraOrdersToSheet() {
  const startedAt = new Date();
  try {
    const response = fetchCultiveraExport_();
    const values = responseToSheetValues_(response);
    if (!values.length || !values[0].length) {
      throw new Error('Cultivera response did not contain tabular data.');
    }

    writeValuesToCultiveraData_(values);
    appendCultiveraSyncLog_('OK', values.length - 1, values[0].length, `Started ${startedAt.toISOString()}`);
    Logger.log(`Synced ${Math.max(0, values.length - 1)} rows and ${values[0].length} columns.`);
  } catch (err) {
    appendCultiveraSyncLog_('ERROR', 0, 0, err.message || String(err));
    throw err;
  }
}

function createCultiveraOrderSyncTrigger() {
  deleteCultiveraOrderSyncTriggers();
  ScriptApp.newTrigger('syncCultiveraOrdersToSheet')
    .timeBased()
    .everyHours(1)
    .create();
}

function deleteCultiveraOrderSyncTriggers() {
  ScriptApp.getProjectTriggers().forEach(trigger => {
    if (trigger.getHandlerFunction() === 'syncCultiveraOrdersToSheet') {
      ScriptApp.deleteTrigger(trigger);
    }
  });
}

function fetchCultiveraExport_() {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty(CULTIVERA_PROP_TOKEN);
  if (!token) {
    throw new Error(`Missing Script Property: ${CULTIVERA_PROP_TOKEN}`);
  }

  const payload = getCultiveraExportPayload_();
  const options = {
    method: 'post',
    contentType: 'application/json;charset=UTF-8',
    payload: JSON.stringify(payload),
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/json, text/plain, */*',
      Origin: 'https://wa.cultiverapro.com',
      Referer: 'https://wa.cultiverapro.com/',
      'x-rts': Math.floor(Date.now() / 1000).toString(),
      'x-tzo': props.getProperty(CULTIVERA_PROP_TZO) || '-420'
    },
    muteHttpExceptions: true
  };

  const response = UrlFetchApp.fetch(CULTIVERA_EXPORT_URL, options);
  const status = response.getResponseCode();
  if (status < 200 || status >= 300) {
    throw new Error(`Cultivera export failed with HTTP ${status}: ${safeTextPreview_(response, 500)}`);
  }
  return response;
}

function getCultiveraExportPayload_() {
  const raw = PropertiesService.getScriptProperties().getProperty(CULTIVERA_PROP_PAYLOAD);
  if (!raw) {
    throw new Error(`Missing Script Property: ${CULTIVERA_PROP_PAYLOAD}. Paste the DevTools Payload JSON there.`);
  }
  try {
    return JSON.parse(raw);
  } catch (err) {
    throw new Error(`${CULTIVERA_PROP_PAYLOAD} is not valid JSON: ${err.message || err}`);
  }
}

function responseToSheetValues_(response) {
  const headers = response.getAllHeaders();
  const contentType = String(
    headerValue_(headers, 'Content-Type') ||
    headerValue_(headers, 'content-type') ||
    ''
  ).toLowerCase();
  const disposition = String(
    headerValue_(headers, 'Content-Disposition') ||
    headerValue_(headers, 'content-disposition') ||
    ''
  ).toLowerCase();

  if (contentType.includes('json')) {
    return jsonToSheetValues_(JSON.parse(response.getContentText()));
  }
  if (contentType.includes('csv') || disposition.includes('.csv')) {
    return Utilities.parseCsv(response.getContentText());
  }
  return excelBlobToSheetValues_(response.getBlob());
}

function excelBlobToSheetValues_(blob) {
  if (typeof Drive === 'undefined' || !Drive.Files) {
    throw new Error('Enable Advanced Google Services > Drive API before importing Excel exports.');
  }

  const tempName = `cultivera-order-export-${new Date().toISOString()}`;
  blob.setName(`${tempName}.xlsx`);

  let convertedFile;
  if (Drive.Files.create) {
    convertedFile = Drive.Files.create(
      { name: tempName, mimeType: MimeType.GOOGLE_SHEETS },
      blob,
      { fields: 'id' }
    );
  } else if (Drive.Files.insert) {
    convertedFile = Drive.Files.insert(
      { title: tempName, mimeType: MimeType.GOOGLE_SHEETS },
      blob,
      { convert: true }
    );
  } else {
    throw new Error('Drive advanced service is enabled, but Files.create/insert is unavailable.');
  }

  try {
    const tempSpreadsheet = SpreadsheetApp.openById(convertedFile.id);
    const sourceSheet = tempSpreadsheet.getSheets()[0];
    return sourceSheet.getDataRange().getValues();
  } finally {
    trashDriveFile_(convertedFile.id);
  }
}

function jsonToSheetValues_(json) {
  const rows = firstArrayInCultiveraJson_(json);
  if (!rows.length) {
    return [['Response'], [JSON.stringify(json)]];
  }

  const headers = [];
  rows.forEach(row => {
    Object.keys(row || {}).forEach(key => {
      if (!headers.includes(key)) {
        headers.push(key);
      }
    });
  });

  return [
    headers,
    ...rows.map(row => headers.map(header => {
      const value = row ? row[header] : '';
      if (value === null || value === undefined) {
        return '';
      }
      return typeof value === 'object' ? JSON.stringify(value) : value;
    }))
  ];
}

function firstArrayInCultiveraJson_(json) {
  if (Array.isArray(json)) {
    return json;
  }
  const candidateKeys = ['data', 'items', 'orders', 'results', 'rows', 'value'];
  for (const key of candidateKeys) {
    if (Array.isArray(json && json[key])) {
      return json[key];
    }
  }
  return [];
}

function writeValuesToCultiveraData_(values) {
  const sheet = getOrCreateCultiveraSheet_(CULTIVERA_DATA_SHEET_NAME);
  sheet.clearContents();
  sheet.getRange(1, 1, values.length, values[0].length).setValues(values);
  sheet.setFrozenRows(1);
}

function getOrCreateCultiveraSheet_(name) {
  const spreadsheet = SpreadsheetApp.openById(CULTIVERA_SPREADSHEET_ID);
  return spreadsheet.getSheetByName(name) || spreadsheet.insertSheet(name);
}

function appendCultiveraSyncLog_(status, rows, columns, message) {
  const sheet = getOrCreateCultiveraSheet_(CULTIVERA_SYNC_LOG_SHEET_NAME);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(['Timestamp', 'Status', 'Rows', 'Columns', 'Message']);
  }
  sheet.appendRow([new Date(), status, rows, columns, message]);
}

function headerValue_(headers, name) {
  if (!headers) {
    return '';
  }
  return headers[name] || headers[String(name).toLowerCase()] || '';
}

function safeTextPreview_(response, limit) {
  try {
    return response.getContentText().slice(0, limit);
  } catch (err) {
    return '[binary response]';
  }
}

function trashDriveFile_(fileId) {
  try {
    DriveApp.getFileById(fileId).setTrashed(true);
  } catch (err) {
    Logger.log(`Could not trash temporary file ${fileId}: ${err.message || err}`);
  }
}
