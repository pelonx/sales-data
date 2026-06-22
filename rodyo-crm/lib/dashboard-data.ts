import { createSupabaseServerClient } from "@/lib/supabase/server";
import { TERRITORY_BRANDS, priorityFromScore, type StoreRollup } from "@/lib/rules";

type TerritoryBrand = (typeof TERRITORY_BRANDS)[number];

type LatestMonthBrandSummary = {
  latestBrandMonth?: string | null;
  latestMonthBrandRevenue: number;
  kSavageLatestMonthRevenue: number;
  mayfieldLatestMonthRevenue: number;
  leisureLandLatestMonthRevenue: number;
};

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
    latestMonth: "2026-06-01",
    latestMonthRevenue: 14620,
    latestBrandMonth: "2026-06-01",
    latestMonthBrandRevenue: 14620,
    kSavageLatestMonthRevenue: 14620,
    mayfieldLatestMonthRevenue: 0,
    leisureLandLatestMonthRevenue: 0,
    kSavageLastActiveRevenue: 14620,
    kSavageMonthlyRunRate: 14620,
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
    latestMonth: "2026-02-01",
    latestMonthRevenue: 0,
    latestBrandMonth: "2026-05-01",
    latestMonthBrandRevenue: 2100,
    kSavageLatestMonthRevenue: 0,
    mayfieldLatestMonthRevenue: 2100,
    leisureLandLatestMonthRevenue: 0,
    kSavageLastActiveRevenue: 6200,
    kSavageMonthlyRunRate: 5200,
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
    latestMonth: null,
    latestMonthRevenue: 0,
    latestBrandMonth: null,
    latestMonthBrandRevenue: 0,
    kSavageLatestMonthRevenue: 0,
    mayfieldLatestMonthRevenue: 0,
    leisureLandLatestMonthRevenue: 0,
    kSavageLastActiveRevenue: 0,
    kSavageMonthlyRunRate: 0,
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

function hasCoordinates(store: StoreRollup) {
  return Number.isFinite(store.latitude) && Number.isFinite(store.longitude);
}

function storePriorityKey(store: StoreRollup) {
  return store.storeId || store.licenseKey || store.license;
}

function isKSavageLapsed(store: StoreRollup) {
  return (
    (store.revenueTotal > 0 || store.kSavageHistoricalRevenue > 0)
    && store.kSavageActiveRevenue <= 0
  );
}

function lapsedPriorityValue(store: StoreRollup) {
  return Math.max(
    store.kSavageMonthlyRunRate || 0,
    store.kSavageLastActiveRevenue || 0,
    store.kSavageHistoricalRevenue || 0,
    store.latestMonthRevenue || 0
  );
}

function priorityLevelsFor(stores: StoreRollup[], valueFor: (store: StoreRollup) => number) {
  const sortedStores = [...stores].sort((left, right) => valueFor(left) - valueFor(right));
  const levels = new Map<string, ReturnType<typeof priorityFromScore>>();

  sortedStores.forEach((store, index) => {
    const score = sortedStores.length === 1 ? 1 : index / (sortedStores.length - 1);
    levels.set(storePriorityKey(store), priorityFromScore(score));
  });

  return levels;
}

function normalizeStoreClassifications(stores: StoreRollup[]) {
  const lapsedStores = stores.filter((store) => hasCoordinates(store) && isKSavageLapsed(store));
  const lapsedLevels = priorityLevelsFor(lapsedStores, lapsedPriorityValue);

  return stores.map((store) => {
    const lapsedLevel = lapsedLevels.get(storePriorityKey(store));
    if (!lapsedLevel) {
      return store;
    }

    return {
      ...store,
      recommendation: "K Savage Lapsed",
      priorityLevel: lapsedLevel,
      mapCategory: `K Savage Lapsed - ${lapsedLevel} Priority`
    };
  });
}

function demoSnapshot(): DashboardSnapshot {
  const stores = normalizeStoreClassifications(demoStores);
  return {
    source: "demo",
    stores,
    metrics: summarize(stores)
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

type LatestRevenueSummary = {
  latestMonth?: string | null;
  latestMonthRevenue: number;
};

function emptyLatestMonthBrandSummary(month?: string | null): LatestMonthBrandSummary {
  return {
    latestBrandMonth: month ?? null,
    latestMonthBrandRevenue: 0,
    kSavageLatestMonthRevenue: 0,
    mayfieldLatestMonthRevenue: 0,
    leisureLandLatestMonthRevenue: 0
  };
}

function monthKeyFromDate(value?: string | null) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  return `${date.getUTCFullYear()}-${month}-01`;
}

function brandContributionKey(brand: TerritoryBrand) {
  if (brand === "K. Savage") {
    return "kSavageLatestMonthRevenue";
  }
  if (brand === "Mayfield") {
    return "mayfieldLatestMonthRevenue";
  }
  return "leisureLandLatestMonthRevenue";
}

function cleanBrand(value: unknown): TerritoryBrand | "" {
  const brand = String(value || "").trim();
  return TERRITORY_BRANDS.includes(brand as TerritoryBrand) ? brand as TerritoryBrand : "";
}

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
  const latestMonthBrandByStore = new Map<string, LatestMonthBrandSummary>();
  const latestRevenueByStore = new Map<string, LatestRevenueSummary>();

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

  const { data: monthlyProfileData } = await supabase
    .from("store_monthly_profile")
    .select("store_id, latest_month, latest_month_revenue");

  (monthlyProfileData || []).forEach((row) => {
    const key = String(row.store_id || "");
    if (!storeKeys.has(key)) {
      return;
    }
    latestRevenueByStore.set(key, {
      latestMonth: row.latest_month,
      latestMonthRevenue: Number(row.latest_month_revenue ?? 0)
    });
  });

  const { data: orderData } = await supabase
    .from("orders")
    .select("store_id, submitted_at, order_items(brand, line_total)")
    .not("store_id", "is", null)
    .not("submitted_at", "is", null);

  (orderData || []).forEach((row) => {
    const key = String(row.store_id || "");
    const orderMonth = monthKeyFromDate(row.submitted_at);
    if (!storeKeys.has(key) || !orderMonth) {
      return;
    }

    const orderItems = Array.isArray(row.order_items) ? row.order_items : [];
    const itemContributions = orderItems.reduce((summary, item) => {
      const brand = cleanBrand(item?.brand);
      const amount = Number(item?.line_total ?? 0);
      if (!brand || amount <= 0) {
        return summary;
      }
      const contributionKey = brandContributionKey(brand);
      summary[contributionKey] += amount;
      summary.latestMonthBrandRevenue += amount;
      return summary;
    }, emptyLatestMonthBrandSummary(orderMonth));

    if (itemContributions.latestMonthBrandRevenue <= 0) {
      return;
    }

    const current = latestMonthBrandByStore.get(key);
    if (!current || orderMonth > String(current.latestBrandMonth || "")) {
      latestMonthBrandByStore.set(key, itemContributions);
      return;
    }
    if (orderMonth === current.latestBrandMonth) {
      current.kSavageLatestMonthRevenue += itemContributions.kSavageLatestMonthRevenue;
      current.mayfieldLatestMonthRevenue += itemContributions.mayfieldLatestMonthRevenue;
      current.leisureLandLatestMonthRevenue += itemContributions.leisureLandLatestMonthRevenue;
      current.latestMonthBrandRevenue += itemContributions.latestMonthBrandRevenue;
    }
  });

  const stores: StoreRollup[] = data.map((row) => {
    const contact = firstFromStoreMap(contactByStore, row);
    const log = firstFromStoreMap(logByStore, row);
    const sample = firstFromStoreMap(sampleByStore, row);
    const latestRevenue = firstFromStoreMap(latestRevenueByStore, row);
    const latestBrandMonth = firstFromStoreMap(latestMonthBrandByStore, row)
      || emptyLatestMonthBrandSummary();
    return {
      storeId: String(row.store_id ?? ""),
      license: String(row.license ?? ""),
      licenseKey: String(row.license_key ?? ""),
      storeName: String(row.store_name ?? "Unnamed Store"),
      address: row.address,
      city: row.city,
      state: row.state,
      zip: row.zip,
      county: row.county,
      latitude: row.latitude === null || row.latitude === undefined ? null : Number(row.latitude),
      longitude: row.longitude === null || row.longitude === undefined ? null : Number(row.longitude),
      googlePlaceId: row.google_place_id,
      territoryRep: row.territory_rep,
      mapCategory: String(row.map_category ?? "No recent brand"),
      recommendation: String(row.recommendation ?? ""),
      priorityLevel: row.priority_level,
      revenueTotal: Number(row.revenue_total ?? 0),
      latestMonth: row.latest_month ?? latestRevenue?.latestMonth ?? null,
      latestMonthRevenue: Number(row.latest_month_revenue ?? latestRevenue?.latestMonthRevenue ?? 0),
      latestBrandMonth: latestBrandMonth.latestBrandMonth ?? null,
      latestMonthBrandRevenue: latestBrandMonth.latestMonthBrandRevenue,
      kSavageLatestMonthRevenue: latestBrandMonth.kSavageLatestMonthRevenue,
      mayfieldLatestMonthRevenue: latestBrandMonth.mayfieldLatestMonthRevenue,
      leisureLandLatestMonthRevenue: latestBrandMonth.leisureLandLatestMonthRevenue,
      kSavageLastActiveRevenue: Number(row.k_savage_last_active_revenue ?? 0),
      kSavageMonthlyRunRate: Number(row.k_savage_monthly_run_rate ?? 0),
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

  const normalizedStores = normalizeStoreClassifications(stores);

  return {
    source: "supabase",
    stores: normalizedStores,
    metrics: summarize(normalizedStores)
  };
}
