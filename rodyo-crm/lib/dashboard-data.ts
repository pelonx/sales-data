import { createSupabaseServerClient } from "@/lib/supabase/server";
import type { StoreRollup } from "@/lib/rules";

export type DashboardSnapshot = {
  source: "demo" | "supabase";
  stores: StoreRollup[];
  metrics: {
    totalRetailers: number;
    mappedStores: number;
    lapsedPriority: number;
    openLanePriority: number;
    pitchMayfield: number;
  };
};

const demoStores: StoreRollup[] = [
  {
    storeId: "demo-zips",
    license: "416999",
    licenseKey: "416999",
    storeName: "Zips Cannabis",
    city: "Tacoma",
    state: "WA",
    zip: "98409",
    county: "Pierce",
    latitude: 47.219,
    longitude: -122.484,
    territoryRep: "DK",
    mapCategory: "Carries K. Savage",
    recommendation: "Maintain K. Savage",
    priorityLevel: "",
    revenueTotal: 61200,
    latestMonthRevenue: 14620,
    marketSalesLastMonth: 93000,
    orders: 8,
    brandRevenue: 18600,
    kSavageActiveRevenue: 4200,
    mayfieldActiveRevenue: 0,
    leisureLandActiveRevenue: 0,
    kSavageHistoricalRevenue: 61200,
    lastOrderAt: "2026-06-12T18:30:00Z",
    lastOrderNumber: "DEMO-1001",
    kSavageLastOrderAt: "2026-06-12T18:30:00Z",
    contactName: "Alex Buyer",
    phoneNumber: "253-555-0101",
    email: "buyer@example.com",
    contactLogCount: 2,
    lastContactDate: "2026-06-15",
    lastContactMethod: "Phone",
    lastContactPerson: "Alex Buyer",
    lastContactNotes: "Follow up on next K. Savage order.",
    sampleDropCount: 1,
    latestSampleDate: "2026-06-01",
    latestSampleBrand: "K. Savage",
    latestSampleProduct: "Demo sample",
    hasContactEver: true,
    hasContactThisMonth: true,
    hasContactThisWeek: false
  },
  {
    storeId: "demo-kush21",
    license: "413221",
    licenseKey: "413221",
    storeName: "Kush21 Sodo",
    city: "Seattle",
    state: "WA",
    zip: "98134",
    county: "King",
    latitude: 47.58,
    longitude: -122.335,
    territoryRep: "CH",
    mapCategory: "K Savage Lapsed - High Priority",
    recommendation: "K Savage Lapsed",
    priorityLevel: "High",
    revenueTotal: 42000,
    latestMonthRevenue: 0,
    marketSalesLastMonth: 122000,
    orders: 4,
    brandRevenue: 7800,
    kSavageActiveRevenue: 0,
    mayfieldActiveRevenue: 2100,
    leisureLandActiveRevenue: 0,
    kSavageHistoricalRevenue: 42000,
    lastOrderAt: "2026-05-22T20:15:00Z",
    lastOrderNumber: "DEMO-1002",
    kSavageLastOrderAt: "2026-02-14T19:10:00Z",
    contactName: null,
    phoneNumber: null,
    email: null,
    contactLogCount: 1,
    lastContactDate: "2026-06-06",
    lastContactMethod: "In-person",
    lastContactPerson: "",
    lastContactNotes: "Buyer requested current menu.",
    sampleDropCount: 0,
    latestSampleDate: null,
    latestSampleBrand: null,
    latestSampleProduct: null,
    hasContactEver: true,
    hasContactThisMonth: false,
    hasContactThisWeek: false
  },
  {
    storeId: "demo-main-street",
    license: "412882",
    licenseKey: "412882",
    storeName: "Main Street Marijuana East",
    city: "Vancouver",
    state: "WA",
    zip: "98682",
    county: "Clark",
    latitude: 45.653,
    longitude: -122.538,
    territoryRep: "DK",
    mapCategory: "Open Lane - High Priority",
    recommendation: "Open lane",
    priorityLevel: "High",
    revenueTotal: 0,
    latestMonthRevenue: 0,
    marketSalesLastMonth: 151000,
    orders: 0,
    brandRevenue: 0,
    kSavageActiveRevenue: 0,
    mayfieldActiveRevenue: 0,
    leisureLandActiveRevenue: 0,
    kSavageHistoricalRevenue: 0,
    lastOrderAt: null,
    lastOrderNumber: null,
    kSavageLastOrderAt: null,
    contactName: null,
    phoneNumber: null,
    email: null,
    contactLogCount: 0,
    lastContactDate: null,
    lastContactMethod: null,
    lastContactPerson: null,
    lastContactNotes: null,
    sampleDropCount: 0,
    latestSampleDate: null,
    latestSampleBrand: null,
    latestSampleProduct: null,
    hasContactEver: false,
    hasContactThisMonth: false,
    hasContactThisWeek: false
  }
];

function summarize(stores: StoreRollup[]) {
  return {
    totalRetailers: stores.length,
    mappedStores: stores.filter((store) => (
      Number.isFinite(store.latitude) && Number.isFinite(store.longitude)
    )).length,
    lapsedPriority: stores.filter((store) => store.mapCategory.startsWith("K Savage Lapsed")).length,
    openLanePriority: stores.filter((store) => store.mapCategory.startsWith("Open Lane")).length,
    pitchMayfield: stores.filter((store) => store.mapCategory === "Pitch Mayfield").length
  };
}

function demoSnapshot(): DashboardSnapshot {
  return {
    source: "demo",
    stores: demoStores,
    metrics: summarize(demoStores)
  };
}

type StoreContactSummary = {
  contactName?: string | null;
  phoneNumber?: string | null;
  email?: string | null;
};

type ContactLogSummary = {
  count: number;
  lastContactDate?: string | null;
  lastContactMethod?: string | null;
  lastContactPerson?: string | null;
  lastContactNotes?: string | null;
};

type SampleDropSummary = {
  count: number;
  latestSampleDate?: string | null;
  latestSampleBrand?: string | null;
  latestSampleProduct?: string | null;
};

function keyFromStore(store: { store_id?: string | null; license_key?: string | null }) {
  return String(store.store_id || store.license_key || "");
}

function keysFromStore(store: { store_id?: string | null; license_key?: string | null }) {
  return [store.store_id, store.license_key].filter(Boolean).map(String);
}

function firstFromStoreMap<T>(map: Map<string, T>, store: { store_id?: string | null; license_key?: string | null }) {
  for (const key of keysFromStore(store)) {
    const value = map.get(key);
    if (value) {
      return value;
    }
  }
  return undefined;
}

export async function loadDashboardSnapshot(): Promise<DashboardSnapshot> {
  if (!process.env.NEXT_PUBLIC_SUPABASE_URL || !process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY) {
    return demoSnapshot();
  }

  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from("crm_store_rollup")
    .select("*")
    .order("market_sales_last_month", { ascending: false, nullsFirst: false })
    .order("store_name", { ascending: true });

  if (error || !data) {
    return demoSnapshot();
  }

  const storeKeys = new Set(data.flatMap((row) => keysFromStore(row)));
  const contactByStore = new Map<string, StoreContactSummary>();
  const logByStore = new Map<string, ContactLogSummary>();
  const sampleByStore = new Map<string, SampleDropSummary>();

  const { data: contactData } = await supabase
    .from("store_contacts")
    .select("store_id, contact_name, phone_number, email");

  (contactData || []).forEach((row) => {
    const key = String(row.store_id || "");
    if (storeKeys.has(key)) {
      contactByStore.set(key, {
        contactName: row.contact_name,
        phoneNumber: row.phone_number,
        email: row.email
      });
    }
  });

  const { data: logData } = await supabase
    .from("contact_logs")
    .select("store_id, license_key, date_contacted, saved_at, contact_method, person_contacted, notes")
    .order("saved_at", { ascending: false });

  (logData || []).forEach((row) => {
    const key = keyFromStore(row);
    if (!storeKeys.has(key)) {
      return;
    }
    const current = logByStore.get(key) || { count: 0 };
    current.count += 1;
    if (!current.lastContactDate) {
      current.lastContactDate = row.date_contacted || row.saved_at || null;
      current.lastContactMethod = row.contact_method;
      current.lastContactPerson = row.person_contacted;
      current.lastContactNotes = row.notes;
    }
    logByStore.set(key, current);
  });

  const { data: sampleData } = await supabase
    .from("sample_drops")
    .select("store_id, sample_date, brand, product_name")
    .order("sample_date", { ascending: false });

  (sampleData || []).forEach((row) => {
    const key = String(row.store_id || "");
    if (!storeKeys.has(key)) {
      return;
    }
    const current = sampleByStore.get(key) || { count: 0 };
    current.count += 1;
    if (!current.latestSampleDate) {
      current.latestSampleDate = row.sample_date;
      current.latestSampleBrand = row.brand;
      current.latestSampleProduct = row.product_name;
    }
    sampleByStore.set(key, current);
  });

  const stores: StoreRollup[] = data.map((row) => {
    const contact = firstFromStoreMap(contactByStore, row);
    const log = firstFromStoreMap(logByStore, row);
    const sample = firstFromStoreMap(sampleByStore, row);
    return {
    storeId: String(row.store_id ?? ""),
    license: String(row.license ?? ""),
    licenseKey: String(row.license_key ?? ""),
    storeName: String(row.store_name ?? "Unnamed Store"),
    city: row.city,
    state: row.state,
    zip: row.zip,
    county: row.county,
    latitude: row.latitude === null || row.latitude === undefined ? null : Number(row.latitude),
    longitude: row.longitude === null || row.longitude === undefined ? null : Number(row.longitude),
    territoryRep: row.territory_rep,
    mapCategory: String(row.map_category ?? "No recent brand"),
    recommendation: String(row.recommendation ?? ""),
    priorityLevel: row.priority_level,
    revenueTotal: Number(row.revenue_total ?? 0),
    latestMonthRevenue: Number(row.latest_month_revenue ?? 0),
    marketSalesLastMonth: Number(row.market_sales_last_month ?? 0),
    orders: Number(row.orders ?? 0),
    brandRevenue: Number(row.brand_revenue ?? 0),
    kSavageActiveRevenue: Number(row.k_savage_active_revenue ?? 0),
    mayfieldActiveRevenue: Number(row.mayfield_active_revenue ?? 0),
    leisureLandActiveRevenue: Number(row.leisure_land_active_revenue ?? 0),
    kSavageHistoricalRevenue: Number(row.k_savage_historical_revenue ?? 0),
    lastOrderAt: row.last_order_at,
    lastOrderNumber: row.last_order_number,
    kSavageLastOrderAt: row.k_savage_last_order_at,
    contactName: contact?.contactName ?? null,
    phoneNumber: contact?.phoneNumber ?? null,
    email: contact?.email ?? null,
    contactLogCount: log?.count ?? 0,
    lastContactDate: log?.lastContactDate ?? null,
    lastContactMethod: log?.lastContactMethod ?? null,
    lastContactPerson: log?.lastContactPerson ?? null,
    lastContactNotes: log?.lastContactNotes ?? null,
    sampleDropCount: sample?.count ?? 0,
    latestSampleDate: sample?.latestSampleDate ?? null,
    latestSampleBrand: sample?.latestSampleBrand ?? null,
    latestSampleProduct: sample?.latestSampleProduct ?? null,
    hasContactEver: Boolean(row.has_contact_ever),
    hasContactThisMonth: Boolean(row.has_contact_this_month),
    hasContactThisWeek: Boolean(row.has_contact_this_week)
    };
  });

  return {
    source: "supabase",
    stores,
    metrics: summarize(stores)
  };
}
