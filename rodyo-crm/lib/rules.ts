export const TERRITORY_BRANDS = ["K. Savage", "Mayfield", "Leisure Land"] as const;

export const TERRITORY_ALL_OTHER_SELECTOR = "All Other Retailers";

export const TERRITORY_SELECTOR_ORDER = [
  "Carries K. Savage",
  "Mayfield placed",
  "Leisure Land Placed",
  "K Savage Lapsed - High Priority",
  "K Savage Lapsed - Medium Priority",
  "K Savage Lapsed - Low Priority",
  "Open Lane - High Priority",
  "Open Lane - Medium Priority",
  "Open Lane - Low Priority",
  "Pitch Mayfield",
  TERRITORY_ALL_OTHER_SELECTOR
] as const;

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
  [TERRITORY_ALL_OTHER_SELECTOR]: "#9AA0A6",
  "No recent brand": "#6E7781",
  "Needs location": "#A8ADB3"
};

export type PriorityLevel = "High" | "Medium" | "Low";

export type StoreRollup = {
  license: string;
  licenseKey: string;
  storeName: string;
  city?: string | null;
  zip?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  territoryRep?: string | null;
  mapCategory: string;
  recommendation: string;
  priorityLevel?: PriorityLevel | "" | null;
  latestMonthRevenue: number;
  marketSalesLastMonth: number;
  kSavageActiveRevenue: number;
  mayfieldActiveRevenue: number;
  leisureLandActiveRevenue: number;
  hasContactEver: boolean;
  hasContactThisMonth: boolean;
  hasContactThisWeek: boolean;
};

export function priorityFromScore(score: number): PriorityLevel {
  if (score >= 0.75) return "High";
  if (score >= 0.4) return "Medium";
  return "Low";
}

export function contactCheckmarks(store: StoreRollup) {
  return {
    ever: store.hasContactEver,
    month: store.hasContactThisMonth,
    week: store.hasContactThisWeek
  };
}

export function isNamedDesignation(store: StoreRollup, designation: string) {
  if (designation === "Carries K. Savage") return store.kSavageActiveRevenue > 0;
  if (designation === "Mayfield placed" || designation === "Carries Mayfield") {
    return store.mayfieldActiveRevenue > 0;
  }
  if (designation === "Leisure Land Placed") return store.leisureLandActiveRevenue > 0;
  return store.mapCategory === designation;
}

export function isAllOtherRetailer(store: StoreRollup, allDesignations = TERRITORY_SELECTOR_ORDER) {
  return !allDesignations.some((designation) => {
    if (designation === TERRITORY_ALL_OTHER_SELECTOR) return false;
    return isNamedDesignation(store, designation);
  });
}

export function formatUsd(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0
  }).format(value || 0);
}
