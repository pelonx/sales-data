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
    license: "416999",
    licenseKey: "416999",
    storeName: "Zips Cannabis",
    city: "Tacoma",
    zip: "98409",
    latitude: 47.219,
    longitude: -122.484,
    territoryRep: "DK",
    mapCategory: "Carries K. Savage",
    recommendation: "Maintain K. Savage",
    priorityLevel: "",
    latestMonthRevenue: 14620,
    marketSalesLastMonth: 93000,
    kSavageActiveRevenue: 4200,
    mayfieldActiveRevenue: 0,
    leisureLandActiveRevenue: 0,
    hasContactEver: true,
    hasContactThisMonth: true,
    hasContactThisWeek: false
  },
  {
    license: "413221",
    licenseKey: "413221",
    storeName: "Kush21 Sodo",
    city: "Seattle",
    zip: "98134",
    latitude: 47.58,
    longitude: -122.335,
    territoryRep: "CH",
    mapCategory: "K Savage Lapsed - High Priority",
    recommendation: "K Savage Lapsed",
    priorityLevel: "High",
    latestMonthRevenue: 0,
    marketSalesLastMonth: 122000,
    kSavageActiveRevenue: 0,
    mayfieldActiveRevenue: 2100,
    leisureLandActiveRevenue: 0,
    hasContactEver: true,
    hasContactThisMonth: false,
    hasContactThisWeek: false
  },
  {
    license: "412882",
    licenseKey: "412882",
    storeName: "Main Street Marijuana East",
    city: "Vancouver",
    zip: "98682",
    latitude: 45.653,
    longitude: -122.538,
    territoryRep: "DK",
    mapCategory: "Open Lane - High Priority",
    recommendation: "Open lane",
    priorityLevel: "High",
    latestMonthRevenue: 0,
    marketSalesLastMonth: 151000,
    kSavageActiveRevenue: 0,
    mayfieldActiveRevenue: 0,
    leisureLandActiveRevenue: 0,
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

  const stores: StoreRollup[] = data.map((row) => ({
    license: String(row.license ?? ""),
    licenseKey: String(row.license_key ?? ""),
    storeName: String(row.store_name ?? "Unnamed Store"),
    city: row.city,
    zip: row.zip,
    latitude: row.latitude === null || row.latitude === undefined ? null : Number(row.latitude),
    longitude: row.longitude === null || row.longitude === undefined ? null : Number(row.longitude),
    territoryRep: row.territory_rep,
    mapCategory: String(row.map_category ?? "No recent brand"),
    recommendation: String(row.recommendation ?? ""),
    priorityLevel: row.priority_level,
    latestMonthRevenue: Number(row.latest_month_revenue ?? 0),
    marketSalesLastMonth: Number(row.market_sales_last_month ?? 0),
    kSavageActiveRevenue: Number(row.k_savage_active_revenue ?? 0),
    mayfieldActiveRevenue: Number(row.mayfield_active_revenue ?? 0),
    leisureLandActiveRevenue: Number(row.leisure_land_active_revenue ?? 0),
    hasContactEver: Boolean(row.has_contact_ever),
    hasContactThisMonth: Boolean(row.has_contact_this_month),
    hasContactThisWeek: Boolean(row.has_contact_this_week)
  }));

  return {
    source: "supabase",
    stores,
    metrics: summarize(stores)
  };
}
