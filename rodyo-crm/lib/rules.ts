export const TERRITORY_BRANDS = ["K. Savage", "Mayfield", "Leisure Land"] as const;

export const TERRITORY_MAP_COLORS: Record<string, string> = {
  "Pitch Mayfield": "#7C5CFF",
  "Mayfield placed": "#E8844C",
  "Carries Mayfield": "#E8844C",
  "Maintain K. Savage": "#FF5AA5",
  "Carries K. Savage": "#FF5AA5",
  "K Savage Lapsed - High Priority": "#B8860B",
  "K Savage Lapsed - Medium Priority": "#FFD23F",
  "K Savage Lapsed - Low Priority": "#FFF3B0",
  "Leisure Land Placed": "#89CFF0",
  "K. Savage blocked": "#D84A4A",
  "Open Lane - High Priority": "#006D2C",
  "Open Lane - Medium Priority": "#31A354",
  "Open Lane - Low Priority": "#A1D99B",
  "No recent brand": "#6E7781",
  "Needs location": "#A8ADB3"
};

export type PriorityLevel = "High" | "Medium" | "Low";

export type StoreRollup = {
  storeId?: string;
  license: string;
  licenseKey: string;
  storeName: string;
  address?: string | null;
  city?: string | null;
  state?: string | null;
  zip?: string | null;
  county?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  googlePlaceId?: string | null;
  territoryRep?: string | null;
  mapCategory: string;
  recommendation: string;
  priorityLevel?: PriorityLevel | "" | null;
  revenueTotal: number;
  latestMonth?: string | null;
  latestMonthRevenue: number;
  latestBrandMonth?: string | null;
  latestMonthBrandRevenue: number;
  kSavageLatestMonthRevenue: number;
  mayfieldLatestMonthRevenue: number;
  leisureLandLatestMonthRevenue: number;
  kSavageLastActiveRevenue: number;
  kSavageMonthlyRunRate: number;
  marketSalesLastMonth: number;
  orders: number;
  brandRevenue: number;
  kSavageActiveRevenue: number;
  mayfieldActiveRevenue: number;
  leisureLandActiveRevenue: number;
  kSavageHistoricalRevenue: number;
  lastOrderAt?: string | null;
  lastOrderNumber?: string | null;
  kSavageLastOrderAt?: string | null;
  contactName?: string | null;
  phoneNumber?: string | null;
  email?: string | null;
  contactLogCount: number;
  lastContactDate?: string | null;
  lastContactMethod?: string | null;
  lastContactPerson?: string | null;
  lastContactNotes?: string | null;
  sampleDropCount: number;
  latestSampleDate?: string | null;
  latestSampleBrand?: string | null;
  latestSampleProduct?: string | null;
  hasContactEver: boolean;
  hasContactThisMonth: boolean;
  hasContactThisWeek: boolean;
};

export type OrderLine = {
  orderId: string;
  orderItemId: string;
  orderNumber: string;
  storeId?: string | null;
  license?: string | null;
  licenseKey?: string | null;
  storeName: string;
  submittedAt?: string | null;
  status?: string | null;
  brand: string;
  productName?: string | null;
  subProductLine?: string | null;
  units: number;
  lineTotal: number;
  importedAt?: string | null;
};

export type SalesGoal = {
  id?: string;
  goalMonth: string;
  goalType: string;
  weekId?: string | null;
  weekLabel?: string | null;
  brand?: string | null;
  goalAmount: number;
  notes?: string | null;
  updatedAt?: string | null;
};

export function priorityFromScore(score: number): PriorityLevel {
  if (score >= 0.75) return "High";
  if (score >= 0.4) return "Medium";
  return "Low";
}

// Days past a store's last K. Savage order before we treat it as having missed
// its expected reorder. Flat threshold (no per-store cadence data yet); tuned to
// catch a roughly-monthly buyer as soon as it misses its cycle, and naturally a
// superset of the 120-day Lapsed classification. Adjust here to change the filter.
export const OVERDUE_REORDER_DAYS = 30;

export function daysSinceDate(value?: string | null): number | null {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return Math.floor((Date.now() - date.getTime()) / 86_400_000);
}

// A store is "Overdue" if it has ordered K. Savage before but has not reordered
// within OVERDUE_REORDER_DAYS. Stores that never carried K. Savage are open-lane
// prospects, not overdue. A K. Savage history with no datable last order means it
// has been overdue long enough to lose the timestamp, so it counts as overdue.
export function isStoreOverdue(store: StoreRollup): boolean {
  if (store.kSavageHistoricalRevenue <= 0) {
    return false;
  }
  const days = daysSinceDate(store.kSavageLastOrderAt);
  return days === null ? true : days > OVERDUE_REORDER_DAYS;
}

export function contactCheckmarks(store: StoreRollup) {
  return {
    ever: store.hasContactEver,
    month: store.hasContactThisMonth,
    week: store.hasContactThisWeek
  };
}

export function formatUsd(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0
  }).format(value || 0);
}
