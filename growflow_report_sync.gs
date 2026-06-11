const GROWFLOW_SPREADSHEET_ID = '1kY5e6SXd7eQ7GJx-jg6M1R60WCCZ9I_25Eb7ZmuDKHw';
const GROWFLOW_DEFAULT_DATA_SHEET_NAME = 'GrowFlow Report';
const GROWFLOW_SYNC_LOG_SHEET_NAME = 'GrowFlow Sync Log';
const GROWFLOW_TRIGGER_HANDLER = 'syncGrowFlowReportToSheet';

const GROWFLOW_TOKEN_URL = 'https://token.growflow.com/oauth/token';
const GROWFLOW_GRAPHQL_URL = 'https://partnerapi.growflow.com/';
const GROWFLOW_AUDIENCE = 'https://growflow.com';
const GROWFLOW_MAX_PAGE_SIZE = 100;
const GROWFLOW_TOKEN_REFRESH_BUFFER_MS = 5 * 60 * 1000;
const GROWFLOW_HTTP_MAX_ATTEMPTS = 4;
const GROWFLOW_DEFAULT_RETRY_SLEEP_MS = 2000;

const GROWFLOW_PROP_CLIENT_ID = 'GROWFLOW_CLIENT_ID';
const GROWFLOW_PROP_CLIENT_SECRET = 'GROWFLOW_CLIENT_SECRET';
const GROWFLOW_PROP_ACCESS_TOKEN = 'GROWFLOW_ACCESS_TOKEN';
const GROWFLOW_PROP_TOKEN_EXPIRES_AT = 'GROWFLOW_TOKEN_EXPIRES_AT';
const GROWFLOW_PROP_GRAPHQL_QUERY = 'GROWFLOW_GRAPHQL_QUERY';
const GROWFLOW_PROP_GRAPHQL_VARIABLES = 'GROWFLOW_GRAPHQL_VARIABLES_JSON';
const GROWFLOW_PROP_PAGINATION_FIELD = 'GROWFLOW_PAGINATION_FIELD';
const GROWFLOW_PROP_ARRAY_EXPAND_PATH = 'GROWFLOW_ARRAY_EXPAND_PATH';
const GROWFLOW_PROP_TARGET_SHEET_NAME = 'GROWFLOW_TARGET_SHEET_NAME';

function checkGrowFlowReportSyncSetup() {
  const props = PropertiesService.getScriptProperties();
  const spreadsheet = SpreadsheetApp.openById(GROWFLOW_SPREADSHEET_ID);
  const sheet = getOrCreateGrowFlowSheet_(getGrowFlowTargetSheetName_());

  Logger.log(`Spreadsheet: ${spreadsheet.getName()}`);
  Logger.log(`Target sheet: ${sheet.getName()}; rows: ${sheet.getLastRow()}; columns: ${sheet.getLastColumn()}`);
  Logger.log(`Active automatic sync triggers: ${getGrowFlowReportSyncTriggers_().length}`);
  Logger.log(`${GROWFLOW_PROP_CLIENT_ID} configured: ${Boolean(props.getProperty(GROWFLOW_PROP_CLIENT_ID))}`);
  Logger.log(`${GROWFLOW_PROP_CLIENT_SECRET} configured: ${Boolean(props.getProperty(GROWFLOW_PROP_CLIENT_SECRET))}`);
  Logger.log(`${GROWFLOW_PROP_GRAPHQL_QUERY} configured: ${Boolean(props.getProperty(GROWFLOW_PROP_GRAPHQL_QUERY))}`);
  Logger.log(`${GROWFLOW_PROP_PAGINATION_FIELD}: ${props.getProperty(GROWFLOW_PROP_PAGINATION_FIELD) || '(auto)'}`);
  Logger.log(`${GROWFLOW_PROP_ARRAY_EXPAND_PATH}: ${props.getProperty(GROWFLOW_PROP_ARRAY_EXPAND_PATH) || '(none)'}`);

  try {
    const variables = getGrowFlowGraphQLVariables_();
    Logger.log(`${GROWFLOW_PROP_GRAPHQL_VARIABLES} keys: ${Object.keys(variables).join(', ') || '(none)'}`);
  } catch (err) {
    Logger.log(`${GROWFLOW_PROP_GRAPHQL_VARIABLES} error: ${err.message || err}`);
  }
}

function setupGrowFlowOrdersExampleConfig() {
  PropertiesService.getScriptProperties().setProperties({
    [GROWFLOW_PROP_TARGET_SHEET_NAME]: 'GrowFlow Orders',
    [GROWFLOW_PROP_PAGINATION_FIELD]: 'orders',
    [GROWFLOW_PROP_ARRAY_EXPAND_PATH]: 'lineItems',
    [GROWFLOW_PROP_GRAPHQL_QUERY]: [
      'query Orders($regionCode: String!, $licenseNumber: String, $skip: Int, $take: Int) {',
      '  orders(regionCode: $regionCode, licenseNumber: $licenseNumber, skip: $skip, take: $take) {',
      '    totalCount',
      '    items {',
      '      poNumber',
      '      stage',
      '      createTimestamp',
      '      vendor {',
      '        licenseNumber',
      '        name',
      '      }',
      '      lineItems {',
      '        complianceId',
      '        total',
      '        price',
      '        quantity',
      '      }',
      '    }',
      '  }',
      '}'
    ].join('\n'),
    [GROWFLOW_PROP_GRAPHQL_VARIABLES]: JSON.stringify({
      regionCode: 'wa',
      licenseNumber: '000021',
      skip: 0,
      take: 100
    }, null, 2)
  }, false);
  Logger.log('Wrote example GrowFlow orders query config. Update licenseNumber before syncing if needed.');
}

function setupGrowFlowInventoryExampleConfig() {
  PropertiesService.getScriptProperties().setProperties({
    [GROWFLOW_PROP_TARGET_SHEET_NAME]: 'GrowFlow Inventory',
    [GROWFLOW_PROP_PAGINATION_FIELD]: 'inventories',
    [GROWFLOW_PROP_ARRAY_EXPAND_PATH]: '',
    [GROWFLOW_PROP_GRAPHQL_QUERY]: [
      'query Inventories($regionCode: String!, $licenseNumber: String, $skip: Int, $take: Int) {',
      '  inventories(regionCode: $regionCode, licenseNumber: $licenseNumber, skip: $skip, take: $take) {',
      '    totalCount',
      '    items {',
      '      birthDate',
      '      complianceId',
      '      remainingQuantity',
      '      status',
      '      unit',
      '      createTimestamp',
      '      product {',
      '        name',
      '        id',
      '        size',
      '        unit',
      '        traceabilityTypeName',
      '        strain {',
      '          name',
      '          id',
      '        }',
      '      }',
      '      room {',
      '        id',
      '        name',
      '      }',
      '    }',
      '  }',
      '}'
    ].join('\n'),
    [GROWFLOW_PROP_GRAPHQL_VARIABLES]: JSON.stringify({
      regionCode: 'wa',
      licenseNumber: '000021',
      skip: 0,
      take: 100
    }, null, 2)
  }, false);
  Logger.log('Wrote example GrowFlow inventory query config. Update licenseNumber before syncing if needed.');
}

function refreshGrowFlowAccessToken() {
  const token = refreshGrowFlowAccessToken_();
  Logger.log(`GrowFlow access token refreshed. Token stored: ${Boolean(token)}; length: ${token.length}.`);
}

function testGrowFlowGraphQLRequest() {
  const result = fetchGrowFlowReportValues_();
  const values = result.values;
  Logger.log(`GrowFlow query field: ${result.label}`);
  Logger.log(`Parsed rows: ${Math.max(0, values.length - 1)}; columns: ${values[0] ? values[0].length : 0}`);
  if (values.length) {
    Logger.log(`Headers: ${values[0].join(' | ')}`);
  }
}

function syncGrowFlowReportToSheet() {
  const startedAt = new Date();
  try {
    const result = fetchGrowFlowReportValues_();
    const values = result.values;
    if (!values.length || !values[0].length) {
      throw new Error('GrowFlow response did not contain tabular data.');
    }

    writeValuesToGrowFlowData_(values);
    appendGrowFlowSyncLog_('OK', Math.max(0, values.length - 1), values[0].length, `${result.label}; started ${startedAt.toISOString()}`);
    Logger.log(`Synced ${Math.max(0, values.length - 1)} rows and ${values[0].length} columns from ${result.label}.`);
  } catch (err) {
    appendGrowFlowSyncLog_('ERROR', 0, 0, err.message || String(err));
    throw err;
  }
}

function createGrowFlowHourlyTrigger() {
  deleteGrowFlowReportSyncTriggers();
  ScriptApp.newTrigger(GROWFLOW_TRIGGER_HANDLER)
    .timeBased()
    .everyHours(1)
    .create();
  Logger.log('Created automatic GrowFlow report sync trigger: every 1 hour.');
}

function deleteGrowFlowReportSyncTriggers() {
  const triggers = getGrowFlowReportSyncTriggers_();
  triggers.forEach(trigger => ScriptApp.deleteTrigger(trigger));
  Logger.log(`Deleted ${triggers.length} automatic GrowFlow report sync trigger(s).`);
}

function listGrowFlowReportSyncTriggers() {
  const triggers = getGrowFlowReportSyncTriggers_();
  Logger.log(`Automatic GrowFlow report sync triggers: ${triggers.length}`);
  triggers.forEach((trigger, i) => {
    Logger.log(
      `Trigger ${i + 1}: handler=${trigger.getHandlerFunction()}; ` +
      `source=${trigger.getTriggerSource()}; event=${trigger.getEventType()}`
    );
  });
}

function getGrowFlowReportSyncTriggers_() {
  return ScriptApp.getProjectTriggers().filter(trigger => (
    trigger.getHandlerFunction() === GROWFLOW_TRIGGER_HANDLER
  ));
}

function fetchGrowFlowReportValues_() {
  const query = getGrowFlowGraphQLQuery_();
  const baseVariables = getGrowFlowGraphQLVariables_();
  const canPage = canGrowFlowAutoPaginate_(query, baseVariables);
  const firstSkip = growFlowPageSkip_(baseVariables);
  const take = growFlowPageTake_(baseVariables);
  const firstJson = fetchGrowFlowGraphQLJson_(query, growFlowVariablesForPage_(baseVariables, query, firstSkip, take));
  const connectionInfo = growFlowFindConnectionInfo_(firstJson.data, getGrowFlowPaginationField_());

  if (!connectionInfo) {
    return {
      label: 'GraphQL data',
      values: growFlowDataToSheetValues_(firstJson.data || firstJson)
    };
  }

  const allItems = connectionInfo.connection.items.slice();
  const totalCount = growFlowNumberOrNull_(connectionInfo.connection.totalCount);
  let pageItemCount = connectionInfo.connection.items.length;
  let nextSkip = firstSkip + pageItemCount;

  while (canPage && pageItemCount > 0 && shouldFetchNextGrowFlowPage_(allItems.length, totalCount, pageItemCount, take)) {
    const pageJson = fetchGrowFlowGraphQLJson_(query, growFlowVariablesForPage_(baseVariables, query, nextSkip, take));
    const pageInfo = growFlowFindConnectionInfo_(pageJson.data, connectionInfo.path);
    pageItemCount = pageInfo.connection.items.length;
    if (!pageItemCount) {
      break;
    }
    allItems.push(...pageInfo.connection.items);
    nextSkip += pageItemCount;
  }

  return {
    label: connectionInfo.path,
    values: growFlowRowsToSheetValues_(allItems)
  };
}

function shouldFetchNextGrowFlowPage_(rowCount, totalCount, lastPageCount, take) {
  if (totalCount !== null) {
    return rowCount < totalCount;
  }
  return take > 0 && lastPageCount >= take;
}

function getGrowFlowGraphQLQuery_() {
  const query = String(PropertiesService.getScriptProperties().getProperty(GROWFLOW_PROP_GRAPHQL_QUERY) || '').trim();
  if (!query) {
    throw new Error(`Missing Script Property: ${GROWFLOW_PROP_GRAPHQL_QUERY}`);
  }
  return query;
}

function getGrowFlowGraphQLVariables_() {
  const raw = PropertiesService.getScriptProperties().getProperty(GROWFLOW_PROP_GRAPHQL_VARIABLES);
  if (!raw) {
    return {};
  }
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('value must be a JSON object');
    }
    return parsed;
  } catch (err) {
    throw new Error(`${GROWFLOW_PROP_GRAPHQL_VARIABLES} is not valid JSON: ${err.message || err}`);
  }
}

function getGrowFlowPaginationField_() {
  return String(PropertiesService.getScriptProperties().getProperty(GROWFLOW_PROP_PAGINATION_FIELD) || '').trim();
}

function getGrowFlowArrayExpandPath_() {
  return String(PropertiesService.getScriptProperties().getProperty(GROWFLOW_PROP_ARRAY_EXPAND_PATH) || '').trim();
}

function getGrowFlowAccessToken_() {
  const props = PropertiesService.getScriptProperties();
  const token = normalizeGrowFlowBearerToken_(props.getProperty(GROWFLOW_PROP_ACCESS_TOKEN));
  const expiresAt = Number(props.getProperty(GROWFLOW_PROP_TOKEN_EXPIRES_AT) || 0);
  if (token && Number.isFinite(expiresAt) && Date.now() + GROWFLOW_TOKEN_REFRESH_BUFFER_MS < expiresAt) {
    return token;
  }
  return refreshGrowFlowAccessToken_();
}

function refreshGrowFlowAccessToken_() {
  const props = PropertiesService.getScriptProperties();
  const clientId = cleanGrowFlowSecret_(props.getProperty(GROWFLOW_PROP_CLIENT_ID));
  const clientSecret = cleanGrowFlowSecret_(props.getProperty(GROWFLOW_PROP_CLIENT_SECRET));
  if (!clientId || !clientSecret) {
    throw new Error(`Missing Script Properties: ${GROWFLOW_PROP_CLIENT_ID} and/or ${GROWFLOW_PROP_CLIENT_SECRET}`);
  }

  const response = fetchGrowFlowWithRetry_(GROWFLOW_TOKEN_URL, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Accept: 'application/json'
    },
    payload: JSON.stringify({
      client_id: clientId,
      client_secret: clientSecret,
      audience: GROWFLOW_AUDIENCE,
      grant_type: 'client_credentials'
    }),
    muteHttpExceptions: true
  }, 'GrowFlow token request');

  const status = response.getResponseCode();
  if (status < 200 || status >= 300) {
    throw new Error(`GrowFlow token request failed with HTTP ${status}: ${growFlowRedactedTextPreview_(response, 800)}`);
  }

  const json = growFlowParseJsonResponse_(response, 'GrowFlow token request');
  const token = normalizeGrowFlowBearerToken_(json.access_token);
  if (!token) {
    throw new Error(`GrowFlow token request succeeded, but no access_token was found. Response preview: ${growFlowRedactedJson_(json)}`);
  }

  const expiresIn = Number(json.expires_in || 86400);
  props.setProperties({
    [GROWFLOW_PROP_ACCESS_TOKEN]: token,
    [GROWFLOW_PROP_TOKEN_EXPIRES_AT]: String(Date.now() + (Number.isFinite(expiresIn) ? expiresIn : 86400) * 1000)
  }, false);
  Logger.log(`GrowFlow token scope: ${json.scope || '(not returned)'}`);
  return token;
}

function fetchGrowFlowGraphQLJson_(query, variables) {
  let token = getGrowFlowAccessToken_();
  let response = fetchGrowFlowGraphQLWithToken_(token, query, variables);
  if (response.getResponseCode() === 401) {
    Logger.log('GrowFlow GraphQL request returned 401. Refreshing token and retrying once.');
    token = refreshGrowFlowAccessToken_();
    response = fetchGrowFlowGraphQLWithToken_(token, query, variables);
  }

  const status = response.getResponseCode();
  if (status < 200 || status >= 300) {
    throw new Error(`GrowFlow GraphQL request failed with HTTP ${status}: ${growFlowRedactedTextPreview_(response, 1200)}`);
  }

  const json = growFlowParseJsonResponse_(response, 'GrowFlow GraphQL request');
  if (json.errors && json.errors.length) {
    throw new Error(`GrowFlow GraphQL errors: ${growFlowGraphQLErrorPreview_(json.errors)}`);
  }
  return json;
}

function fetchGrowFlowGraphQLWithToken_(token, query, variables) {
  return fetchGrowFlowWithRetry_(GROWFLOW_GRAPHQL_URL, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Accept: 'application/json',
      Authorization: `Bearer ${token}`
    },
    payload: JSON.stringify({
      query: query,
      variables: variables || {}
    }),
    muteHttpExceptions: true
  }, 'GrowFlow GraphQL request');
}

function fetchGrowFlowWithRetry_(url, options, label) {
  let lastResponse = null;
  for (let attempt = 0; attempt < GROWFLOW_HTTP_MAX_ATTEMPTS; attempt++) {
    lastResponse = UrlFetchApp.fetch(url, options);
    const status = lastResponse.getResponseCode();
    if (status !== 429 && status < 500) {
      return lastResponse;
    }
    if (attempt >= GROWFLOW_HTTP_MAX_ATTEMPTS - 1) {
      return lastResponse;
    }

    const sleepMs = growFlowRetrySleepMs_(lastResponse, attempt);
    Logger.log(`${label} returned HTTP ${status}. Waiting ${Math.round(sleepMs / 1000)}s before retry ${attempt + 2}.`);
    Utilities.sleep(sleepMs);
  }
  return lastResponse;
}

function growFlowRetrySleepMs_(response, attempt) {
  const retryAfter = growFlowHeaderValue_(response.getAllHeaders(), 'Retry-After');
  const retryAfterSeconds = Number(retryAfter);
  if (Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0) {
    return Math.min(retryAfterSeconds * 1000, 60000);
  }

  const retryAfterDate = Date.parse(retryAfter);
  if (Number.isFinite(retryAfterDate)) {
    return Math.min(Math.max(0, retryAfterDate - Date.now()), 60000);
  }

  const backoffMs = GROWFLOW_DEFAULT_RETRY_SLEEP_MS * Math.pow(2, attempt);
  return Math.min(backoffMs + Math.floor(Math.random() * 500), 60000);
}

function growFlowVariablesForPage_(baseVariables, query, skip, take) {
  const variables = Object.assign({}, baseVariables);
  if (growFlowQueryUsesVariable_(query, 'skip') || Object.prototype.hasOwnProperty.call(baseVariables, 'skip')) {
    variables.skip = skip;
  }
  if (growFlowQueryUsesVariable_(query, 'take') || Object.prototype.hasOwnProperty.call(baseVariables, 'take')) {
    variables.take = take;
  }
  return variables;
}

function canGrowFlowAutoPaginate_(query, variables) {
  return growFlowQueryUsesVariable_(query, 'skip') || Object.prototype.hasOwnProperty.call(variables, 'skip');
}

function growFlowQueryUsesVariable_(query, name) {
  return new RegExp(`\\$${name}\\b`).test(query);
}

function growFlowPageSkip_(variables) {
  const skip = Number(variables.skip || 0);
  return Number.isFinite(skip) && skip >= 0 ? skip : 0;
}

function growFlowPageTake_(variables) {
  const take = Number(variables.take || GROWFLOW_MAX_PAGE_SIZE);
  if (!Number.isFinite(take) || take <= 0) {
    return GROWFLOW_MAX_PAGE_SIZE;
  }
  return Math.min(Math.floor(take), GROWFLOW_MAX_PAGE_SIZE);
}

function growFlowFindConnectionInfo_(data, requestedPath) {
  if (!data || typeof data !== 'object') {
    return null;
  }

  if (requestedPath) {
    const requested = growFlowValueAtPath_(data, requestedPath);
    if (!isGrowFlowConnection_(requested)) {
      throw new Error(`${GROWFLOW_PROP_PAGINATION_FIELD}=${requestedPath} did not point to a GraphQL field with an items array.`);
    }
    return {
      path: requestedPath,
      connection: requested
    };
  }

  for (const key of Object.keys(data)) {
    if (isGrowFlowConnection_(data[key])) {
      return {
        path: key,
        connection: data[key]
      };
    }
  }
  return growFlowFindNestedConnectionInfo_(data, '');
}

function growFlowFindNestedConnectionInfo_(value, path) {
  if (!value || typeof value !== 'object') {
    return null;
  }
  if (isGrowFlowConnection_(value)) {
    return {
      path: path,
      connection: value
    };
  }
  for (const key of Object.keys(value)) {
    const childPath = path ? `${path}.${key}` : key;
    const found = growFlowFindNestedConnectionInfo_(value[key], childPath);
    if (found) {
      return found;
    }
  }
  return null;
}

function isGrowFlowConnection_(value) {
  return Boolean(value && typeof value === 'object' && Array.isArray(value.items));
}

function growFlowDataToSheetValues_(data) {
  if (Array.isArray(data)) {
    return growFlowRowsToSheetValues_(data);
  }

  const rows = firstArrayInGrowFlowJson_(data);
  if (rows.length) {
    return growFlowRowsToSheetValues_(rows);
  }

  if (data && typeof data === 'object') {
    return growFlowRowsToSheetValues_([data]);
  }
  return [['Value'], [growFlowCellValue_(data)]];
}

function growFlowRowsToSheetValues_(rows) {
  if (!rows.length) {
    return [['No rows returned']];
  }

  if (!rows.every(row => row && typeof row === 'object' && !Array.isArray(row))) {
    return [['Value'], ...rows.map(value => [growFlowCellValue_(value)])];
  }

  const expandedRows = expandGrowFlowRows_(rows, getGrowFlowArrayExpandPath_());
  const flatRows = expandedRows.map(row => growFlowFlattenRow_(row, ''));
  const headers = [];
  flatRows.forEach(row => {
    Object.keys(row).forEach(key => {
      if (!headers.includes(key)) {
        headers.push(key);
      }
    });
  });

  if (!headers.length) {
    return [['Value'], ...flatRows.map(() => [''])];
  }

  return [
    headers,
    ...flatRows.map(row => headers.map(header => growFlowCellValue_(row[header])))
  ];
}

function expandGrowFlowRows_(rows, expandPath) {
  if (!expandPath) {
    return rows;
  }

  const expanded = [];
  rows.forEach(row => {
    const children = growFlowValueAtPath_(row, expandPath);
    const base = growFlowFlattenRow_(row, expandPath);
    if (!Array.isArray(children) || !children.length) {
      expanded.push(base);
      return;
    }

    children.forEach(child => {
      const childFlat = {};
      growFlowFlattenValue_(child, expandPath, childFlat, '');
      expanded.push(Object.assign({}, base, childFlat));
    });
  });
  return expanded;
}

function growFlowFlattenRow_(row, skipPath) {
  const out = {};
  growFlowFlattenValue_(row, '', out, skipPath);
  return out;
}

function growFlowFlattenValue_(value, prefix, out, skipPath) {
  if (skipPath && prefix === skipPath) {
    return;
  }
  if (value === null || value === undefined) {
    if (prefix) {
      out[prefix] = '';
    }
    return;
  }

  if (Array.isArray(value)) {
    if (prefix) {
      out[prefix] = value.length ? JSON.stringify(value) : '';
    }
    return;
  }

  if (value instanceof Date) {
    out[prefix] = value;
    return;
  }

  if (typeof value !== 'object') {
    if (prefix) {
      out[prefix] = value;
    }
    return;
  }

  const keys = Object.keys(value);
  if (!keys.length && prefix) {
    out[prefix] = '';
    return;
  }

  keys.forEach(key => {
    const childPrefix = prefix ? `${prefix}.${key}` : key;
    growFlowFlattenValue_(value[key], childPrefix, out, skipPath);
  });
}

function firstArrayInGrowFlowJson_(value) {
  if (Array.isArray(value)) {
    return value;
  }
  if (!value || typeof value !== 'object') {
    return [];
  }

  const candidateKeys = ['items', 'data', 'nodes', 'edges', 'results', 'rows', 'value'];
  for (const key of candidateKeys) {
    if (Array.isArray(value[key])) {
      return value[key];
    }
  }

  for (const key of Object.keys(value)) {
    const found = firstArrayInGrowFlowJson_(value[key]);
    if (found.length) {
      return found;
    }
  }
  return [];
}

function writeValuesToGrowFlowData_(values) {
  const sheet = getOrCreateGrowFlowSheet_(getGrowFlowTargetSheetName_());
  ensureGrowFlowSheetSize_(sheet, values.length, values[0].length);
  sheet.clearContents();
  sheet.getRange(1, 1, values.length, values[0].length).setValues(values);
  sheet.setFrozenRows(1);
}

function ensureGrowFlowSheetSize_(sheet, rowCount, columnCount) {
  if (sheet.getMaxRows() < rowCount) {
    sheet.insertRowsAfter(sheet.getMaxRows(), rowCount - sheet.getMaxRows());
  }
  if (sheet.getMaxColumns() < columnCount) {
    sheet.insertColumnsAfter(sheet.getMaxColumns(), columnCount - sheet.getMaxColumns());
  }
}

function getGrowFlowTargetSheetName_() {
  return String(
    PropertiesService.getScriptProperties().getProperty(GROWFLOW_PROP_TARGET_SHEET_NAME) ||
    GROWFLOW_DEFAULT_DATA_SHEET_NAME
  ).trim();
}

function getOrCreateGrowFlowSheet_(name) {
  const spreadsheet = SpreadsheetApp.openById(GROWFLOW_SPREADSHEET_ID);
  return spreadsheet.getSheetByName(name) || spreadsheet.insertSheet(name);
}

function appendGrowFlowSyncLog_(status, rows, columns, message) {
  const sheet = getOrCreateGrowFlowSheet_(GROWFLOW_SYNC_LOG_SHEET_NAME);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(['Timestamp', 'Status', 'Rows', 'Columns', 'Message']);
  }
  sheet.appendRow([new Date(), status, rows, columns, message]);
}

function growFlowValueAtPath_(value, path) {
  if (!path) {
    return value;
  }
  return path.split('.').reduce((current, part) => (
    current && typeof current === 'object' ? current[part] : undefined
  ), value);
}

function growFlowNumberOrNull_(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function growFlowParseJsonResponse_(response, label) {
  try {
    return JSON.parse(response.getContentText());
  } catch (err) {
    throw new Error(`${label} returned non-JSON content: ${growFlowRedactedTextPreview_(response, 800)}`);
  }
}

function growFlowGraphQLErrorPreview_(errors) {
  return errors.map(error => (
    error && error.message ? error.message : JSON.stringify(error)
  )).join('; ').slice(0, 1200);
}

function growFlowHeaderValue_(headers, name) {
  if (!headers) {
    return '';
  }
  return headers[name] || headers[String(name).toLowerCase()] || '';
}

function growFlowCellValue_(value) {
  if (value === null || value === undefined) {
    return '';
  }
  if (typeof value === 'object' && !(value instanceof Date)) {
    return JSON.stringify(value);
  }
  return value;
}

function cleanGrowFlowSecret_(value) {
  let cleaned = String(value || '').trim();
  if (
    (cleaned.startsWith('"') && cleaned.endsWith('"')) ||
    (cleaned.startsWith("'") && cleaned.endsWith("'"))
  ) {
    cleaned = cleaned.slice(1, -1).trim();
  }
  return cleaned;
}

function normalizeGrowFlowBearerToken_(value) {
  return cleanGrowFlowSecret_(value).replace(/^Bearer\s+/i, '').trim();
}

function growFlowSafeTextPreview_(response, limit) {
  try {
    return response.getContentText().slice(0, limit);
  } catch (err) {
    return '[binary response]';
  }
}

function growFlowRedactedTextPreview_(response, limit) {
  return growFlowSafeTextPreview_(response, limit)
    .replace(/Bearer\s+[A-Za-z0-9._-]+/gi, 'Bearer REDACTED')
    .replace(/([A-Za-z0-9_-]+\.){2}[A-Za-z0-9_-]+/g, 'JWT_REDACTED')
    .replace(/("(?:client_secret|api[_-]?key|token|access_token|accessToken|bearer_token|bearerToken|jwt)"\s*:\s*")[^"]+(")/gi, '$1REDACTED$2');
}

function growFlowRedactedJson_(json) {
  return JSON.stringify(json)
    .replace(/"(?:client_secret|api[_-]?key|token|access_token|accessToken|bearer_token|bearerToken|jwt)"\s*:\s*"[^"]+"/gi, '"redacted":"REDACTED"')
    .slice(0, 800);
}
