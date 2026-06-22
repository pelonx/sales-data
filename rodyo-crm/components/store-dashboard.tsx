"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  Check,
  ExternalLink,
  ListPlus,
  Map as MapIcon,
  Plus,
  SlidersHorizontal,
  Trash2,
  X
} from "lucide-react";
import type { DashboardSnapshot } from "@/lib/dashboard-data";
import {
  TERRITORY_BRANDS,
  TERRITORY_MAP_COLORS,
  formatUsd,
  type StoreRollup
} from "@/lib/rules";

type StoreDashboardProps = {
  snapshot: DashboardSnapshot;
};

type ViewMode = "stores" | "map";
type DetailTab = "contact" | "orders" | "buyer" | "history" | "samples";
type SortKey = "store" | "brand" | "priority" | "balaclava" | "storeRevenue" | "rep" | "log";
type SortDirection = "asc" | "desc";
type BalaclavaSalesFilter = "all" | "1000" | "5000";
type StoreRevenueFilter = "all" | "300" | "50000" | "100000";
type BrandFilter = (typeof TERRITORY_BRANDS)[number];
type ParetoFilter = "all" | "top30" | "eighty";
type PriorityFilter = "all" | "lapsed" | "open-lane";
type MapLibreModule = typeof import("maplibre-gl");
type MapLibreMap = import("maplibre-gl").Map;
type MapLibreMarker = import("maplibre-gl").Marker;

type StoreFilters = {
  balaclavaSales: BalaclavaSalesFilter;
  storeRevenue: StoreRevenueFilter;
  brand: BrandFilter[];
  pareto: ParetoFilter;
  priority: PriorityFilter;
  region: string;
};

type BuyerContactPatch = {
  contactName: string | null;
  phoneNumber: string | null;
  email: string | null;
};

type ContactLogPatch = {
  storeId: string;
  dateContacted: string | null;
  contactMethod: string | null;
  initials: string | null;
  personContacted: string | null;
  notes: string | null;
  savedAt: string | null;
};

const defaultStoreFilters: StoreFilters = {
  balaclavaSales: "all",
  storeRevenue: "all",
  brand: [],
  pareto: "all",
  priority: "all",
  region: "all"
};

const detailTabs: { id: DetailTab; label: string }[] = [
  { id: "contact", label: "Contact" },
  { id: "orders", label: "Orders" },
  { id: "buyer", label: "Buyer" },
  { id: "history", label: "History" },
  { id: "samples", label: "Samples" }
];

const sortableColumns: { key: SortKey; label: string; width?: string }[] = [
  { key: "store", label: "Store", width: "32%" },
  { key: "brand", label: "Brand" },
  { key: "priority", label: "Priority", width: "8%" },
  { key: "balaclava", label: "Balaclava" },
  { key: "storeRevenue", label: "Store Revenue" },
  { key: "rep", label: "Rep" },
  { key: "log", label: "Log" }
];

const BRAND_DOT_COLORS: Record<BrandFilter, string> = {
  "K. Savage": TERRITORY_MAP_COLORS["Carries K. Savage"],
  Mayfield: TERRITORY_MAP_COLORS["Mayfield placed"],
  "Leisure Land": TERRITORY_MAP_COLORS["Leisure Land Placed"]
};

const DEFAULT_ROUTE_START = {
  label: "Tacoma, WA",
  latitude: 47.2529,
  longitude: -122.4443
};
const GOOGLE_MAPS_ROUTE_STOP_LIMIT = 10;
const ROUTE_LINE_SOURCE_ID = "trip-route-line-source";
const ROUTE_LINE_CASING_LAYER_ID = "trip-route-line-casing";
const ROUTE_LINE_LAYER_ID = "trip-route-line";
type Coordinates = {
  latitude: number;
  longitude: number;
};
type RouteStart = Coordinates & {
  label: string;
};
type RouteSuggestion = {
  store: StoreRollup;
  alongRouteMiles: number;
  offRouteMiles: number;
};
type RouteGeometryResponse = {
  coordinates?: [number, number][];
};

function FilterLabel({ active, children }: { active: boolean; children: ReactNode }) {
  return (
    <label className={active ? "filter-label is-active" : "filter-label"}>
      <span>{children}</span>
      {active ? <Check aria-label="Filter applied" size={13} /> : null}
    </label>
  );
}

function CheckState({ active, label }: { active: boolean; label: string }) {
  return (
    <span className="tag" title={label}>
      <Check size={14} color={active ? "var(--green)" : "var(--muted)"} />
      {label}
    </span>
  );
}

function summarizeStores(stores: StoreRollup[]) {
  return {
    totalRetailers: stores.length,
    mappedStores: stores.filter((store) => (
      Number.isFinite(store.latitude) && Number.isFinite(store.longitude)
    )).length,
    lapsedPriority: stores.filter((store) => matchesPriorityFilter(store, "lapsed")).length,
    openLanePriority: stores.filter((store) => matchesPriorityFilter(store, "open-lane")).length,
    pitchMayfield: stores.filter((store) => store.mapCategory === "Pitch Mayfield").length
  };
}

function storeKey(store: StoreRollup) {
  return store.storeId || store.licenseKey || store.license;
}

function hasStoreCoordinates(store: StoreRollup) {
  return Number.isFinite(store.latitude) && Number.isFinite(store.longitude);
}

function storeCoordinates(store: StoreRollup) {
  return {
    latitude: Number(store.latitude),
    longitude: Number(store.longitude)
  };
}

function milesBetween(
  left: Coordinates,
  right: Coordinates
) {
  const earthRadiusMiles = 3958.8;
  const toRadians = (value: number) => value * (Math.PI / 180);
  const latitudeDelta = toRadians(right.latitude - left.latitude);
  const longitudeDelta = toRadians(right.longitude - left.longitude);
  const leftLatitude = toRadians(left.latitude);
  const rightLatitude = toRadians(right.latitude);
  const haversine = Math.sin(latitudeDelta / 2) ** 2
    + Math.cos(leftLatitude) * Math.cos(rightLatitude) * Math.sin(longitudeDelta / 2) ** 2;
  return earthRadiusMiles * 2 * Math.atan2(Math.sqrt(haversine), Math.sqrt(1 - haversine));
}

function optimizeTripStores(stores: StoreRollup[], startLocation: Coordinates = DEFAULT_ROUTE_START) {
  const remaining = stores.filter(hasStoreCoordinates);
  const ordered: StoreRollup[] = [];
  let currentLocation: Coordinates = startLocation;

  while (remaining.length) {
    let closestIndex = 0;
    let closestMiles = Number.POSITIVE_INFINITY;
    remaining.forEach((store, index) => {
      const miles = milesBetween(currentLocation, storeCoordinates(store));
      if (miles < closestMiles) {
        closestMiles = miles;
        closestIndex = index;
      }
    });

    const [nextStore] = remaining.splice(closestIndex, 1);
    ordered.push(nextStore);
    currentLocation = storeCoordinates(nextStore);
  }

  return ordered;
}

function estimatedTripMiles(stores: StoreRollup[], startLocation: Coordinates = DEFAULT_ROUTE_START) {
  let totalMiles = 0;
  let currentLocation: Coordinates = startLocation;
  stores.forEach((store) => {
    const nextLocation = storeCoordinates(store);
    totalMiles += milesBetween(currentLocation, nextLocation);
    currentLocation = nextLocation;
  });
  return totalMiles;
}

function mapsCoordinate(store: StoreRollup) {
  return `${Number(store.latitude)},${Number(store.longitude)}`;
}

function coordinateParam(coordinates: Coordinates) {
  return `${coordinates.latitude},${coordinates.longitude}`;
}

function routeTextPart(value?: string | null) {
  return String(value || "").trim();
}

function storeRouteQuery(store: StoreRollup) {
  const cityState = [routeTextPart(store.city), routeTextPart(store.state)]
    .filter(Boolean)
    .join(", ");
  const address = [routeTextPart(store.address), cityState, routeTextPart(store.zip)]
    .filter(Boolean)
    .join(", ");
  const namedLocation = [routeTextPart(store.storeName), address]
    .filter(Boolean)
    .join(", ");

  return namedLocation || mapsCoordinate(store);
}

function googleMapsRouteUrl(stores: StoreRollup[], startLocation: RouteStart = DEFAULT_ROUTE_START) {
  const routeStores = stores.slice(0, GOOGLE_MAPS_ROUTE_STOP_LIMIT);
  if (!routeStores.length) {
    return "";
  }

  const destination = routeStores[routeStores.length - 1];
  const waypointStores = routeStores.slice(0, -1);
  const params = new URLSearchParams({
    api: "1",
    origin: coordinateParam(startLocation),
    destination: storeRouteQuery(destination),
    travelmode: "driving"
  });
  if (destination.googlePlaceId) {
    params.set("destination_place_id", destination.googlePlaceId);
  }
  if (waypointStores.length) {
    params.set("waypoints", waypointStores.map(storeRouteQuery).join("|"));
    if (waypointStores.every((store) => store.googlePlaceId)) {
      params.set("waypoint_place_ids", waypointStores.map((store) => String(store.googlePlaceId)).join("|"));
    }
  }
  return `https://www.google.com/maps/dir/?${params.toString()}`;
}

function routeLineData(
  stores: StoreRollup[],
  startLocation: Coordinates = DEFAULT_ROUTE_START,
  roadCoordinates?: [number, number][] | null,
  isLoadingRoadRoute = false
) {
  const straightCoordinates = [
    [startLocation.longitude, startLocation.latitude],
    ...stores
      .filter(hasStoreCoordinates)
      .map((store) => [Number(store.longitude), Number(store.latitude)])
  ];
  const coordinates = isLoadingRoadRoute
    ? []
    : roadCoordinates && roadCoordinates.length > 1
      ? roadCoordinates
      : straightCoordinates;

  return {
    type: "FeatureCollection" as const,
    features: coordinates.length > 1
      ? [
        {
          type: "Feature" as const,
          properties: {},
          geometry: {
            type: "LineString" as const,
            coordinates
          }
        }
      ]
      : []
  };
}

async function fetchRoadRouteCoordinates(
  origin: Coordinates,
  stops: Coordinates[],
  signal?: AbortSignal
) {
  if (!stops.length) {
    return null;
  }

  const response = await fetch("/api/route-line", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ origin, stops }),
    signal
  });

  if (!response.ok) {
    return null;
  }

  const result = (await response.json()) as RouteGeometryResponse;
  const coordinates = Array.isArray(result.coordinates)
    ? result.coordinates.filter((coordinate) => (
      Array.isArray(coordinate)
      && coordinate.length === 2
      && coordinate.every((value) => Number.isFinite(value))
    ))
    : [];

  return coordinates.length > 1 ? coordinates : null;
}

function routeProjection(start: Coordinates, destination: Coordinates, point: Coordinates) {
  const averageLatitude = ((start.latitude + destination.latitude + point.latitude) / 3) * (Math.PI / 180);
  const milesPerLatitudeDegree = 69;
  const milesPerLongitudeDegree = Math.cos(averageLatitude) * 69.172;
  const destinationX = (destination.longitude - start.longitude) * milesPerLongitudeDegree;
  const destinationY = (destination.latitude - start.latitude) * milesPerLatitudeDegree;
  const pointX = (point.longitude - start.longitude) * milesPerLongitudeDegree;
  const pointY = (point.latitude - start.latitude) * milesPerLatitudeDegree;
  const routeLengthSquared = destinationX ** 2 + destinationY ** 2;

  if (!routeLengthSquared) {
    return {
      alongRouteMiles: 0,
      offRouteMiles: milesBetween(start, point),
      progress: 0
    };
  }

  const progress = ((pointX * destinationX) + (pointY * destinationY)) / routeLengthSquared;
  const clampedProgress = Math.max(0, Math.min(1, progress));
  const projectedX = destinationX * clampedProgress;
  const projectedY = destinationY * clampedProgress;
  const routeLength = Math.sqrt(routeLengthSquared);

  return {
    alongRouteMiles: routeLength * clampedProgress,
    offRouteMiles: Math.sqrt((pointX - projectedX) ** 2 + (pointY - projectedY) ** 2),
    progress
  };
}

function coordinatePairToPoint([longitude, latitude]: [number, number]): Coordinates {
  return { latitude, longitude };
}

function routePolylineProjection(routeCoordinates: [number, number][], point: Coordinates) {
  if (routeCoordinates.length < 2) {
    return null;
  }

  let bestProjection = {
    alongRouteMiles: 0,
    offRouteMiles: Number.POSITIVE_INFINITY,
    progress: 0
  };
  let completedMiles = 0;
  let totalMiles = 0;

  for (let index = 0; index < routeCoordinates.length - 1; index += 1) {
    const start = coordinatePairToPoint(routeCoordinates[index]);
    const end = coordinatePairToPoint(routeCoordinates[index + 1]);
    const segmentMiles = milesBetween(start, end);
    const projection = routeProjection(start, end, point);
    const alongSegmentMiles = Math.max(0, Math.min(segmentMiles, projection.alongRouteMiles));

    if (projection.offRouteMiles < bestProjection.offRouteMiles) {
      bestProjection = {
        alongRouteMiles: completedMiles + alongSegmentMiles,
        offRouteMiles: projection.offRouteMiles,
        progress: 0
      };
    }

    completedMiles += segmentMiles;
    totalMiles += segmentMiles;
  }

  return {
    ...bestProjection,
    progress: totalMiles ? bestProjection.alongRouteMiles / totalMiles : 0
  };
}

function suggestedRouteStops({
  stores,
  currentRouteStores,
  destinationStore,
  maxOffRouteMiles,
  maxStops,
  startLocation,
  routeCoordinates
}: {
  stores: StoreRollup[];
  currentRouteStores: StoreRollup[];
  destinationStore?: StoreRollup;
  maxOffRouteMiles: number;
  maxStops: number;
  startLocation: Coordinates;
  routeCoordinates?: [number, number][] | null;
}): RouteSuggestion[] {
  if (!destinationStore || !hasStoreCoordinates(destinationStore) || maxStops <= 0) {
    return [];
  }

  const routeKeys = new Set(currentRouteStores.map(storeKey));
  const destination = storeCoordinates(destinationStore);
  const rankedSuggestions = stores
    .filter((store) => hasStoreCoordinates(store) && !routeKeys.has(storeKey(store)))
    .map((store) => {
      const point = storeCoordinates(store);
      const projection = routeCoordinates && routeCoordinates.length > 1
        ? routePolylineProjection(routeCoordinates, point) || routeProjection(startLocation, destination, point)
        : routeProjection(startLocation, destination, point);
      return {
        store,
        alongRouteMiles: projection.alongRouteMiles,
        offRouteMiles: projection.offRouteMiles,
        progress: projection.progress
      };
    })
    .filter((suggestion) => (
      suggestion.progress >= 0
      && suggestion.progress <= 1
      && suggestion.offRouteMiles <= maxOffRouteMiles
    ))
    .sort((left, right) => (
      prioritySortValue(right.store) - prioritySortValue(left.store)
      || right.store.marketSalesLastMonth - left.store.marketSalesLastMonth
      || left.offRouteMiles - right.offRouteMiles
      || left.alongRouteMiles - right.alongRouteMiles
    ))
    .slice(0, maxStops);

  return rankedSuggestions
    .sort((left, right) => left.alongRouteMiles - right.alongRouteMiles)
    .map(({ store, alongRouteMiles, offRouteMiles }) => ({ store, alongRouteMiles, offRouteMiles }));
}

function textSortValue(value?: string | null) {
  return String(value || "").trim().toLowerCase();
}

function sortValueForStore(store: StoreRollup, sortKey: SortKey) {
  if (sortKey === "store") {
    return `${textSortValue(store.storeName)} ${textSortValue(store.license)}`;
  }
  if (sortKey === "brand") {
    return brandPlacements(store).join(" ");
  }
  if (sortKey === "priority") {
    return prioritySortValue(store);
  }
  if (sortKey === "balaclava") {
    return store.latestMonthRevenue;
  }
  if (sortKey === "storeRevenue") {
    return store.marketSalesLastMonth;
  }
  if (sortKey === "rep") {
    return textSortValue(store.territoryRep);
  }
  return store.hasContactEver ? 1 : 0;
}

function sortStores(stores: StoreRollup[], sortKey: SortKey, direction: SortDirection) {
  const directionMultiplier = direction === "asc" ? 1 : -1;

  return [...stores].sort((left, right) => {
    const leftValue = sortValueForStore(left, sortKey);
    const rightValue = sortValueForStore(right, sortKey);
    let comparison = 0;

    if (typeof leftValue === "number" && typeof rightValue === "number") {
      comparison = leftValue - rightValue;
    } else {
      comparison = String(leftValue).localeCompare(String(rightValue), undefined, {
        numeric: true,
        sensitivity: "base"
      });
    }

    if (comparison === 0) {
      comparison = textSortValue(left.storeName).localeCompare(textSortValue(right.storeName), undefined, {
        numeric: true,
        sensitivity: "base"
      });
    }

    return comparison * directionMultiplier;
  });
}

function priorityText(store: StoreRollup) {
  return `${store.mapCategory} ${store.recommendation}`.toLowerCase();
}

function matchesPriorityFilter(store: StoreRollup, priority: PriorityFilter) {
  const text = priorityText(store);
  if (priority === "lapsed") {
    return text.includes("lapsed");
  }
  if (priority === "open-lane") {
    return text.includes("open lane");
  }
  return true;
}

function priorityRank(store: StoreRollup) {
  if (!store.mapCategory.includes("Priority")) {
    return 0;
  }
  if (store.priorityLevel === "High") {
    return 3;
  }
  if (store.priorityLevel === "Medium") {
    return 2;
  }
  if (store.priorityLevel === "Low") {
    return 1;
  }
  return 0;
}

function prioritySortValue(store: StoreRollup) {
  const laneRank = matchesPriorityFilter(store, "lapsed")
    ? 2
    : matchesPriorityFilter(store, "open-lane")
    ? 1
    : 0;
  return laneRank * 10 + priorityRank(store);
}

function PriorityDot({ store }: { store: StoreRollup }) {
  const rank = priorityRank(store);
  if (!rank) {
    return <span aria-label="No priority status" className="priority-empty" />;
  }

  return (
    <span
      aria-label={`${store.mapCategory}`}
      className="priority-dot"
      style={{ background: TERRITORY_MAP_COLORS[store.mapCategory] ?? "var(--muted)" }}
      title={store.mapCategory}
    />
  );
}

function matchesBrandFilter(store: StoreRollup, brand: BrandFilter) {
  if (brand === "K. Savage") {
    return store.kSavageActiveRevenue > 0;
  }
  if (brand === "Mayfield") {
    return store.mayfieldActiveRevenue > 0;
  }
  if (brand === "Leisure Land") {
    return store.leisureLandActiveRevenue > 0;
  }
  return true;
}

function brandPlacements(store: StoreRollup) {
  return TERRITORY_BRANDS.filter((brand) => matchesBrandFilter(store, brand));
}

function BrandPlacementDots({ store }: { store: StoreRollup }) {
  const brands = brandPlacements(store);

  return (
    <span
      aria-label={brands.length ? `Brand placement: ${brands.join(", ")}` : "No brand placement"}
      className="brand-dots"
      title={brands.length ? brands.join(", ") : "No brand placement"}
    >
      {brands.map((brand) => (
        <span
          aria-hidden="true"
          className="brand-dot"
          key={brand}
          style={{ background: BRAND_DOT_COLORS[brand] ?? "var(--muted)" }}
        />
      ))}
    </span>
  );
}

function normalizeBrandFilters(value: StoreFilters["brand"] | BrandFilter | "all" | undefined) {
  if (Array.isArray(value)) {
    return value.filter((brand): brand is BrandFilter => (
      TERRITORY_BRANDS.includes(brand as BrandFilter)
    ));
  }
  if (value && value !== "all" && TERRITORY_BRANDS.includes(value as BrandFilter)) {
    return [value as BrandFilter];
  }
  return [];
}

function brandFilterLabel(brands: BrandFilter[]) {
  if (!brands.length) {
    return "All brands";
  }
  if (brands.length === 1) {
    return brands[0];
  }
  return `${brands.length} brands`;
}

function applyStoreFilters(stores: StoreRollup[], filters: StoreFilters) {
  let nextStores = stores;

  if (filters.balaclavaSales !== "all") {
    const minimum = Number(filters.balaclavaSales);
    nextStores = nextStores.filter((store) => store.latestMonthRevenue >= minimum);
  }

  if (filters.storeRevenue !== "all") {
    const minimum = Number(filters.storeRevenue);
    nextStores = nextStores.filter((store) => store.marketSalesLastMonth >= minimum);
  }

  const brandFilters = normalizeBrandFilters(filters.brand);
  if (brandFilters.length) {
    nextStores = nextStores.filter((store) => (
      brandFilters.some((brand) => matchesBrandFilter(store, brand))
    ));
  }

  if (filters.priority !== "all") {
    nextStores = nextStores.filter((store) => matchesPriorityFilter(store, filters.priority));
  }

  if (filters.region !== "all") {
    nextStores = nextStores.filter((store) => textSortValue(store.county) === filters.region);
  }

  if (filters.pareto === "top30") {
    const topKeys = new Set(
      [...nextStores]
        .sort((left, right) => right.marketSalesLastMonth - left.marketSalesLastMonth)
        .slice(0, 30)
        .map(storeKey)
    );
    nextStores = nextStores.filter((store) => topKeys.has(storeKey(store)));
  } else if (filters.pareto === "eighty") {
    const sortedByRevenue = [...nextStores].sort(
      (left, right) => right.marketSalesLastMonth - left.marketSalesLastMonth
    );
    const totalRevenue = sortedByRevenue.reduce((total, store) => total + store.marketSalesLastMonth, 0);
    const paretoKeys = new Set<string>();
    let cumulativeRevenue = 0;

    for (const store of sortedByRevenue) {
      if (totalRevenue <= 0) {
        break;
      }
      paretoKeys.add(storeKey(store));
      cumulativeRevenue += store.marketSalesLastMonth;
      if (cumulativeRevenue / totalRevenue >= 0.8) {
        break;
      }
    }

    nextStores = nextStores.filter((store) => paretoKeys.has(storeKey(store)));
  }

  return nextStores;
}

function countActiveFilters(filters: StoreFilters) {
  return [
    filters.balaclavaSales !== "all",
    filters.storeRevenue !== "all",
    normalizeBrandFilters(filters.brand).length > 0,
    filters.pareto !== "all",
    filters.priority !== "all",
    filters.region !== "all"
  ].filter(Boolean).length;
}

function formatDate(value?: string | null) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(date);
}

function formatMonth(value?: string | null) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    year: "numeric",
    timeZone: "UTC"
  }).format(date);
}

function localDateInputValue(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function localDateFromInput(value?: string | null) {
  const date = value ? new Date(`${value}T00:00:00`) : new Date();
  return Number.isNaN(date.getTime()) ? new Date() : date;
}

function startOfWeek(date: Date) {
  const start = new Date(date);
  const daysSinceMonday = (start.getDay() + 6) % 7;
  start.setDate(start.getDate() - daysSinceMonday);
  start.setHours(0, 0, 0, 0);
  return start;
}

function isContactThisMonth(dateValue?: string | null) {
  const contactDate = localDateFromInput(dateValue);
  const today = new Date();
  return (
    contactDate.getFullYear() === today.getFullYear()
    && contactDate.getMonth() === today.getMonth()
  );
}

function isContactThisWeek(dateValue?: string | null) {
  const contactDate = localDateFromInput(dateValue);
  contactDate.setHours(0, 0, 0, 0);
  const weekStart = startOfWeek(new Date());
  const nextWeekStart = new Date(weekStart);
  nextWeekStart.setDate(nextWeekStart.getDate() + 7);
  return contactDate >= weekStart && contactDate < nextWeekStart;
}

function DetailStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
    </div>
  );
}

function latestMonthBrandContributions(store: StoreRollup) {
  return [
    {
      brand: "K. Savage" as BrandFilter,
      value: store.kSavageLatestMonthRevenue
    },
    {
      brand: "Mayfield" as BrandFilter,
      value: store.mayfieldLatestMonthRevenue
    },
    {
      brand: "Leisure Land" as BrandFilter,
      value: store.leisureLandLatestMonthRevenue
    }
  ];
}

function latestBalaclavaMonthLabel(store: StoreRollup) {
  return formatMonth(store.latestMonth || store.latestBrandMonth || store.kSavageLastOrderAt);
}

function LatestMonthStat({ store }: { store: StoreRollup }) {
  const brandTotal = store.latestMonthBrandRevenue || 0;
  const total = brandTotal > 0 ? brandTotal : store.latestMonthRevenue;
  const showContributions = brandTotal > 0;
  const latestMonthLabel = latestBalaclavaMonthLabel(store);

  return (
    <div className="metric latest-month-card">
      <div className="metric-label">{latestMonthLabel ? `Latest Month: ${latestMonthLabel}` : "Latest Month"}</div>
      {showContributions ? (
        <div className="brand-contributions">
          {latestMonthBrandContributions(store).map((contribution) => (
            <div className="brand-contribution-row" key={contribution.brand}>
              <span>
                <span
                  aria-hidden="true"
                  className="brand-dot mini"
                  style={{ background: BRAND_DOT_COLORS[contribution.brand] ?? "var(--muted)" }}
                />
                {contribution.brand}
              </span>
              <strong>{formatUsd(contribution.value)}</strong>
            </div>
          ))}
        </div>
      ) : null}
      <div className="metric-value">{formatUsd(total)}</div>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value?: string | number | null }) {
  return (
    <div className="detail-row">
      <span>{label}</span>
      <strong>{value === null || value === undefined || value === "" ? "-" : value}</strong>
    </div>
  );
}

function StoreDetailSummary({ store }: { store: StoreRollup }) {
  const location = [store.city, store.state, store.zip].filter(Boolean).join(", ");
  const latestMonthLabel = latestBalaclavaMonthLabel(store);

  return (
    <div className="detail-summary">
      <div className="detail-list compact">
        <DetailRow label="License" value={store.license} />
        <DetailRow label="Rep" value={store.territoryRep} />
        <DetailRow label="Location" value={location} />
        <DetailRow
          label={latestMonthLabel ? `Latest Balaclava (${latestMonthLabel})` : "Latest Balaclava"}
          value={formatUsd(store.latestMonthRevenue)}
        />
        <DetailRow label="Market sales" value={formatUsd(store.marketSalesLastMonth)} />
        <DetailRow label="Orders" value={store.orders.toLocaleString()} />
        <DetailRow label="Log entries" value={store.contactLogCount.toLocaleString()} />
      </div>
    </div>
  );
}

function BuyerEditor({
  store,
  onSaved
}: {
  store: StoreRollup;
  onSaved: (storeId: string, buyer: BuyerContactPatch) => void;
}) {
  const [contactName, setContactName] = useState(store.contactName ?? "");
  const [phoneNumber, setPhoneNumber] = useState(store.phoneNumber ?? "");
  const [email, setEmail] = useState(store.email ?? "");
  const [isSaving, setIsSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    setContactName(store.contactName ?? "");
    setPhoneNumber(store.phoneNumber ?? "");
    setEmail(store.email ?? "");
    setMessage("");
  }, [store.contactName, store.email, store.phoneNumber, store.storeId]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!store.storeId) {
      setMessage("This store is missing a Supabase store id.");
      return;
    }

    setIsSaving(true);
    setMessage("");

    try {
      const response = await fetch("/api/store-contacts", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          storeId: store.storeId,
          contactName,
          phoneNumber,
          email
        })
      });
      const result = await response.json();

      if (!response.ok) {
        throw new Error(result.error || "Could not save buyer contact.");
      }

      onSaved(result.storeId, {
        contactName: result.contactName,
        phoneNumber: result.phoneNumber,
        email: result.email
      });
      setMessage("Saved");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not save buyer contact.");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <form className="detail-stack" onSubmit={handleSubmit}>
      <div className="form-grid">
        <div className="field">
          <label>Buyer</label>
          <input
            value={contactName}
            onChange={(event) => setContactName(event.target.value)}
            placeholder="Buyer name"
          />
        </div>
        <div className="field">
          <label>Phone</label>
          <input
            value={phoneNumber}
            onChange={(event) => setPhoneNumber(event.target.value)}
            placeholder="Phone number"
            type="tel"
          />
        </div>
        <div className="field">
          <label>Email</label>
          <input
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="Email address"
            type="email"
          />
        </div>
      </div>
      <button className="primary-button detail-save-button" type="submit" disabled={isSaving}>
        {isSaving ? "Saving..." : "Save Buyer"}
      </button>
      {message ? <div className="status-message">{message}</div> : null}
      <div className="detail-list">
        <DetailRow label="License" value={store.license} />
        <DetailRow label="Rep" value={store.territoryRep} />
        <DetailRow label="County" value={store.county} />
        <DetailRow label="Location" value={[store.city, store.state, store.zip].filter(Boolean).join(", ")} />
      </div>
    </form>
  );
}

function ContactLogForm({
  store,
  onSaved
}: {
  store: StoreRollup;
  onSaved: (storeId: string, contactLog: ContactLogPatch) => void;
}) {
  const [dateContacted, setDateContacted] = useState(localDateInputValue());
  const [contactMethod, setContactMethod] = useState("");
  const [initials, setInitials] = useState(store.territoryRep ?? "");
  const [personContacted, setPersonContacted] = useState(store.contactName ?? "");
  const [notes, setNotes] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    setDateContacted(localDateInputValue());
    setContactMethod("");
    setInitials(store.territoryRep ?? "");
    setPersonContacted(store.contactName ?? "");
    setNotes("");
    setMessage("");
  }, [store.contactName, store.storeId, store.territoryRep]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!store.storeId) {
      setMessage("This store is missing a Supabase store id.");
      return;
    }

    setIsSaving(true);
    setMessage("");

    try {
      const response = await fetch("/api/contact-logs", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          storeId: store.storeId,
          license: store.license,
          licenseKey: store.licenseKey,
          storeName: store.storeName,
          dateContacted,
          contactMethod,
          initials,
          personContacted,
          notes
        })
      });
      const result = await response.json();

      if (!response.ok) {
        throw new Error(result.error || "Could not save contact log.");
      }

      onSaved(result.storeId, {
        storeId: result.storeId,
        dateContacted: result.dateContacted,
        contactMethod: result.contactMethod,
        initials: result.initials,
        personContacted: result.personContacted,
        notes: result.notes,
        savedAt: result.savedAt
      });
      setNotes("");
      setMessage("Saved");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not save contact log.");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <form className="detail-stack" onSubmit={handleSubmit}>
      <div className="detail-tabs">
        <CheckState active={store.hasContactEver} label="Any log" />
        <CheckState active={store.hasContactThisMonth} label="This month" />
        <CheckState active={store.hasContactThisWeek} label="This week" />
      </div>
      <div className="form-grid">
        <div className="field">
          <label>Date contacted</label>
          <input
            value={dateContacted}
            onChange={(event) => setDateContacted(event.target.value)}
            type="date"
          />
        </div>
        <div className="field">
          <label>Contact method</label>
          <select
            value={contactMethod}
            onChange={(event) => setContactMethod(event.target.value)}
          >
            <option value="">Select</option>
            <option>In-person</option>
            <option>Phone</option>
            <option>Email</option>
            <option>Text</option>
          </select>
        </div>
        <div className="field">
          <label>Initials</label>
          <input
            value={initials}
            onChange={(event) => setInitials(event.target.value.toUpperCase())}
            placeholder="Rep initials"
          />
        </div>
        <div className="field">
          <label>Person contacted</label>
          <input
            value={personContacted}
            onChange={(event) => setPersonContacted(event.target.value)}
            placeholder="Buyer or staff name"
          />
        </div>
      </div>
      <div className="field">
        <label>Notes</label>
        <textarea
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          placeholder="What happened, next step, objection, commitment..."
          rows={4}
        />
      </div>
      <button className="primary-button detail-save-button" type="submit" disabled={isSaving}>
        {isSaving ? "Saving..." : "Save Contact Log"}
      </button>
      {message ? <div className="status-message">{message}</div> : null}
    </form>
  );
}

function createPopupContent(store: StoreRollup) {
  const container = document.createElement("div");
  container.className = "map-popup";

  const title = document.createElement("strong");
  title.textContent = store.storeName;
  container.appendChild(title);

  const license = document.createElement("span");
  license.textContent = `${store.license} · ${store.city || "No city"}`;
  container.appendChild(license);

  const brands = document.createElement("span");
  const placedBrands = brandPlacements(store);
  brands.textContent = placedBrands.length ? `Brands ${placedBrands.join(", ")}` : "No brand placement";
  container.appendChild(brands);

  const revenue = document.createElement("span");
  revenue.textContent = `Balaclava ${formatUsd(store.latestMonthRevenue)} · Market ${formatUsd(store.marketSalesLastMonth)}`;
  container.appendChild(revenue);

  return container;
}

function StoreMap({
  stores,
  routeStart = DEFAULT_ROUTE_START,
  routeStores = [],
  selectedStore,
  onSelect
}: {
  stores: StoreRollup[];
  routeStart?: RouteStart;
  routeStores?: StoreRollup[];
  selectedStore?: StoreRollup;
  onSelect: (storeKeyValue: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const maplibreRef = useRef<MapLibreModule | null>(null);
  const markersRef = useRef<Map<string, { marker: MapLibreMarker; element: HTMLButtonElement }>>(new Map());
  const routeMarkersRef = useRef<Map<string, MapLibreMarker>>(new Map());
  const [isMapReady, setIsMapReady] = useState(false);
  const [roadRouteCoordinates, setRoadRouteCoordinates] = useState<[number, number][] | null | undefined>(null);
  const mappedStores = useMemo(() => stores.filter(hasStoreCoordinates), [stores]);
  const selectedStoreKey = selectedStore ? storeKey(selectedStore) : "";
  const selectedStoreKeyRef = useRef(selectedStoreKey);
  const routeStopCoordinates = useMemo(() => (
    routeStores.filter(hasStoreCoordinates).map(storeCoordinates)
  ), [routeStores]);
  const mappedStoreSignature = useMemo(() => mappedStores.map(storeKey).join("|"), [mappedStores]);
  const routeStoreSignature = useMemo(() => (
    routeStores.filter(hasStoreCoordinates).map(storeKey).join("|")
  ), [routeStores]);
  const routeCoordinateSignature = useMemo(() => (
    routeStopCoordinates.map((coordinates) => `${coordinates.latitude},${coordinates.longitude}`).join("|")
  ), [routeStopCoordinates]);
  const routeData = useMemo(
    () => routeLineData(
      routeStores,
      routeStart,
      roadRouteCoordinates,
      routeStopCoordinates.length > 0 && roadRouteCoordinates === undefined
    ),
    [
      roadRouteCoordinates,
      routeCoordinateSignature,
      routeStart.latitude,
      routeStart.longitude,
      routeStoreSignature,
      routeStopCoordinates.length
    ]
  );

  useEffect(() => {
    let cancelled = false;

    async function initializeMap() {
      const maplibregl = await import("maplibre-gl");
      if (cancelled || !containerRef.current || mapRef.current) {
        return;
      }

      maplibreRef.current = maplibregl;
      const map = new maplibregl.Map({
        container: containerRef.current,
        center: [-120.7401, 47.7511],
        zoom: 6,
        attributionControl: false,
        style: {
          version: 8,
          sources: {
            osm: {
              type: "raster",
              tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
              tileSize: 256,
              attribution: "© OpenStreetMap contributors"
            }
          },
          layers: [
            {
              id: "osm",
              type: "raster",
              source: "osm"
            }
          ]
        }
      });

      map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
      map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
      map.on("load", () => {
        if (!cancelled) {
          setIsMapReady(true);
        }
      });
      mapRef.current = map;
    }

    initializeMap();

    return () => {
      cancelled = true;
      markersRef.current.forEach(({ marker }) => marker.remove());
      markersRef.current.clear();
      routeMarkersRef.current.forEach((marker) => marker.remove());
      routeMarkersRef.current.clear();
      mapRef.current?.remove();
      mapRef.current = null;
      maplibreRef.current = null;
      setIsMapReady(false);
    };
  }, []);

  useEffect(() => {
    if (!routeStopCoordinates.length) {
      setRoadRouteCoordinates(null);
      return;
    }

    const controller = new AbortController();
    setRoadRouteCoordinates(undefined);

    async function fetchRoadRoute() {
      try {
        setRoadRouteCoordinates(await fetchRoadRouteCoordinates(
          routeStart,
          routeStopCoordinates,
          controller.signal
        ));
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setRoadRouteCoordinates(null);
        }
      }
    }

    fetchRoadRoute();

    return () => controller.abort();
  }, [routeCoordinateSignature, routeStart.latitude, routeStart.longitude]);

  useEffect(() => {
    const map = mapRef.current;
    const maplibregl = maplibreRef.current;
    if (!map || !maplibregl || !isMapReady) {
      return;
    }

    markersRef.current.forEach(({ marker }) => marker.remove());
    markersRef.current.clear();

    mappedStores.forEach((store) => {
      const key = storeKey(store);
      const element = document.createElement("button");
      element.type = "button";
      element.className = `map-marker${key === selectedStoreKeyRef.current ? " is-selected" : ""}`;
      element.style.background = TERRITORY_MAP_COLORS[store.mapCategory] ?? "var(--muted)";
      element.setAttribute("aria-label", `Select ${store.storeName}`);
      element.addEventListener("click", () => onSelect(key));

      const popup = new maplibregl.Popup({
        closeButton: false,
        closeOnClick: false,
        offset: 16
      }).setDOMContent(createPopupContent(store));

      const marker = new maplibregl.Marker({
        element,
        anchor: "center"
      })
        .setLngLat([Number(store.longitude), Number(store.latitude)])
        .setPopup(popup)
        .addTo(map);

      markersRef.current.set(key, { marker, element });
    });

    if (routeStopCoordinates.length) {
      return;
    }

    if (mappedStores.length === 1) {
      map.easeTo({
        center: [Number(mappedStores[0].longitude), Number(mappedStores[0].latitude)],
        zoom: 11,
        duration: 500
      });
    } else if (mappedStores.length > 1) {
      const bounds = new maplibregl.LngLatBounds();
      mappedStores.forEach((store) => {
        bounds.extend([Number(store.longitude), Number(store.latitude)]);
      });
      map.fitBounds(bounds, {
        padding: 54,
        maxZoom: 10,
        duration: 500
      });
    }
  }, [isMapReady, mappedStoreSignature, onSelect]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isMapReady) {
      return;
    }

    if (!map.getSource(ROUTE_LINE_SOURCE_ID)) {
      map.addSource(ROUTE_LINE_SOURCE_ID, {
        type: "geojson",
        data: routeData
      });
      map.addLayer({
        id: ROUTE_LINE_CASING_LAYER_ID,
        type: "line",
        source: ROUTE_LINE_SOURCE_ID,
        layout: {
          "line-cap": "round",
          "line-join": "round"
        },
        paint: {
          "line-color": "#101418",
          "line-opacity": 0.72,
          "line-width": 8
        }
      });
      map.addLayer({
        id: ROUTE_LINE_LAYER_ID,
        type: "line",
        source: ROUTE_LINE_SOURCE_ID,
        layout: {
          "line-cap": "round",
          "line-join": "round"
        },
        paint: {
          "line-color": "#7dc2ae",
          "line-opacity": 0.92,
          "line-width": 4
        }
      });
      return;
    }

    const source = map.getSource(ROUTE_LINE_SOURCE_ID) as { setData?: (data: typeof routeData) => void } | undefined;
    source?.setData?.(routeData);
  }, [isMapReady, routeData]);

  useEffect(() => {
    const map = mapRef.current;
    const maplibregl = maplibreRef.current;
    if (!map || !maplibregl || !isMapReady) {
      return;
    }
    const mapInstance = map;
    const maplibre = maplibregl;

    routeMarkersRef.current.forEach((marker) => marker.remove());
    routeMarkersRef.current.clear();

    const routeStops = routeStores.filter(hasStoreCoordinates);
    if (!routeStops.length) {
      return;
    }

    function addRouteMarker(
      key: string,
      label: string,
      title: string,
      coordinates: Coordinates,
      tone: "start" | "waypoint" | "end",
      selectStoreKey?: string
    ) {
      const element = document.createElement(selectStoreKey ? "button" : "div");
      element.className = `route-marker route-marker-${tone}`;
      element.textContent = label;
      element.title = title;
      element.setAttribute("aria-label", title);
      if (selectStoreKey && element instanceof HTMLButtonElement) {
        element.type = "button";
        element.addEventListener("click", () => onSelect(selectStoreKey));
      }

      const marker = new maplibre.Marker({
        element,
        anchor: "center"
      })
        .setLngLat([coordinates.longitude, coordinates.latitude])
        .addTo(mapInstance);

      routeMarkersRef.current.set(key, marker);
    }

    addRouteMarker("start", "S", `Start: ${routeStart.label || "Custom start"}`, routeStart, "start");
    routeStops.forEach((store, index) => {
      const isEnd = index === routeStops.length - 1;
      addRouteMarker(
        storeKey(store),
        isEnd ? "E" : String(index + 1),
        `${isEnd ? "End" : `Waypoint ${index + 1}`}: ${store.storeName}`,
        storeCoordinates(store),
        isEnd ? "end" : "waypoint",
        storeKey(store)
      );
    });
  }, [
    isMapReady,
    onSelect,
    routeStart.label,
    routeStart.latitude,
    routeStart.longitude,
    routeStoreSignature
  ]);

  useEffect(() => {
    selectedStoreKeyRef.current = selectedStoreKey;
    markersRef.current.forEach(({ element }, key) => {
      element.classList.toggle("is-selected", key === selectedStoreKey);
    });
  }, [selectedStoreKey]);

  return (
    <div className="store-map">
      <div ref={containerRef} className="map-canvas" />
      {!mappedStores.length ? (
        <div className="map-empty">No filtered stores have coordinates yet.</div>
      ) : null}
    </div>
  );
}

function TripPlanner({
  stores,
  selectedStore,
  activeTab,
  setActiveTab,
  routeDestinationKey,
  tripStoreKeys,
  onAddWaypoint,
  onAddWaypoints,
  onRemoveStore,
  onClearTrip,
  onSetDestination,
  onSelectStore,
  onBuyerSaved,
  onContactLogSaved
}: {
  stores: StoreRollup[];
  selectedStore?: StoreRollup;
  activeTab: DetailTab;
  setActiveTab: (tab: DetailTab) => void;
  routeDestinationKey: string;
  tripStoreKeys: string[];
  onAddWaypoint: (key: string) => void;
  onAddWaypoints: (keys: string[]) => void;
  onRemoveStore: (key: string) => void;
  onClearTrip: () => void;
  onSetDestination: (key: string) => void;
  onSelectStore: (key: string) => void;
  onBuyerSaved: (storeId: string, buyer: BuyerContactPatch) => void;
  onContactLogSaved: (storeId: string, contactLog: ContactLogPatch) => void;
}) {
  const [routeStart, setRouteStart] = useState<RouteStart>(DEFAULT_ROUTE_START);
  const [maxOffRouteMiles, setMaxOffRouteMiles] = useState(5);
  const [maxSuggestedStops, setMaxSuggestedStops] = useState(6);
  const [destinationRouteCoordinates, setDestinationRouteCoordinates] = useState<[number, number][] | null>(null);
  const mappedStores = useMemo(() => stores.filter(hasStoreCoordinates), [stores]);
  const mappedStoreByKey = useMemo(() => {
    const byKey = new Map<string, StoreRollup>();
    mappedStores.forEach((store) => byKey.set(storeKey(store), store));
    return byKey;
  }, [mappedStores]);
  const selectedKeys = useMemo(() => new Set(tripStoreKeys), [tripStoreKeys]);
  const destinationStore = routeDestinationKey ? mappedStoreByKey.get(routeDestinationKey) : undefined;
  const tripStores = useMemo(() => (
    tripStoreKeys
      .map((key) => mappedStoreByKey.get(key))
      .filter((store): store is StoreRollup => Boolean(store))
  ), [mappedStoreByKey, tripStoreKeys]);
  const farthestRouteStoreKey = useMemo(() => {
    let farthestKey = "";
    let farthestMiles = -1;
    tripStores.forEach((store) => {
      const distanceFromStart = milesBetween(routeStart, storeCoordinates(store));
      if (distanceFromStart > farthestMiles) {
        farthestMiles = distanceFromStart;
        farthestKey = storeKey(store);
      }
    });
    return farthestKey;
  }, [routeStart, tripStores]);
  const waypointStores = useMemo(() => (
    tripStoreKeys
      .filter((key) => key !== routeDestinationKey)
      .map((key) => mappedStoreByKey.get(key))
      .filter((store): store is StoreRollup => Boolean(store))
  ), [mappedStoreByKey, routeDestinationKey, tripStoreKeys]);
  const orderedTripStores = useMemo(() => {
    const orderedWaypoints = optimizeTripStores(waypointStores, routeStart);
    if (destinationStore) {
      return [...orderedWaypoints, destinationStore];
    }
    return optimizeTripStores(tripStores, routeStart);
  }, [destinationStore, routeStart, tripStores, waypointStores]);
  const unselectedCandidateStores = useMemo(() => (
    mappedStores.filter((store) => !selectedKeys.has(storeKey(store)))
  ), [mappedStores, selectedKeys]);
  const candidateStores = useMemo(() => unselectedCandidateStores.slice(0, 80), [unselectedCandidateStores]);
  const routeSuggestions = useMemo(() => suggestedRouteStops({
    stores: mappedStores,
    currentRouteStores: orderedTripStores,
    destinationStore,
    maxOffRouteMiles,
    maxStops: maxSuggestedStops,
    startLocation: routeStart,
    routeCoordinates: destinationRouteCoordinates
  }), [
    destinationRouteCoordinates,
    destinationStore,
    mappedStores,
    maxOffRouteMiles,
    maxSuggestedStops,
    orderedTripStores,
    routeStart
  ]);
  const estimatedMiles = estimatedTripMiles(orderedTripStores, routeStart);
  const routeUrl = googleMapsRouteUrl(orderedTripStores, routeStart);
  const launchStopCount = Math.min(orderedTripStores.length, GOOGLE_MAPS_ROUTE_STOP_LIMIT);
  const tripBalaclava = orderedTripStores.reduce((total, store) => total + store.latestMonthRevenue, 0);
  const tripMarket = orderedTripStores.reduce((total, store) => total + store.marketSalesLastMonth, 0);
  const selectedStoreKey = selectedStore ? storeKey(selectedStore) : "";
  const canAddSelectedStore = Boolean(
    selectedStore && hasStoreCoordinates(selectedStore)
  );
  const isSelectedStoreInRoute = Boolean(selectedStoreKey && selectedKeys.has(selectedStoreKey));

  function updateRouteStartLabel(label: string) {
    setRouteStart((currentStart) => ({ ...currentStart, label }));
  }

  function updateRouteStartCoordinate(key: "latitude" | "longitude", value: number) {
    if (!Number.isFinite(value)) {
      return;
    }
    setRouteStart((currentStart) => ({ ...currentStart, [key]: value }));
  }

  function handleAddRouteStore(nextStoreKey: string) {
    const nextStore = mappedStoreByKey.get(nextStoreKey);
    if (!nextStore) {
      return;
    }

    const currentEndStore = farthestRouteStoreKey ? mappedStoreByKey.get(farthestRouteStoreKey) : undefined;
    const nextStoreMiles = milesBetween(routeStart, storeCoordinates(nextStore));
    const currentEndMiles = currentEndStore ? milesBetween(routeStart, storeCoordinates(currentEndStore)) : -1;

    if (!currentEndStore || nextStoreMiles > currentEndMiles) {
      onSetDestination(nextStoreKey);
      return;
    }

    onAddWaypoint(nextStoreKey);
  }

  function handleRemoveRouteStore(nextStoreKey: string) {
    onRemoveStore(nextStoreKey);
  }

  useEffect(() => {
    if (!destinationStore || !hasStoreCoordinates(destinationStore)) {
      setDestinationRouteCoordinates(null);
      return;
    }

    const destination = destinationStore;
    const controller = new AbortController();
    setDestinationRouteCoordinates(null);

    async function fetchDestinationRoute() {
      try {
        setDestinationRouteCoordinates(await fetchRoadRouteCoordinates(
          routeStart,
          [storeCoordinates(destination)],
          controller.signal
        ));
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setDestinationRouteCoordinates(null);
        }
      }
    }

    fetchDestinationRoute();

    return () => controller.abort();
  }, [destinationStore, routeStart.latitude, routeStart.longitude]);

  useEffect(() => {
    if (farthestRouteStoreKey && routeDestinationKey !== farthestRouteStoreKey) {
      onSetDestination(farthestRouteStoreKey);
    }
  }, [farthestRouteStoreKey, onSetDestination, routeDestinationKey]);

  return (
    <section className="trip-layout">
      <div className="panel map-panel trip-map-panel">
        <div className="panel-header">
          <h3>Store Map</h3>
          <span className="table-meta">
            {mappedStores.length.toLocaleString()} mapped · {orderedTripStores.length.toLocaleString()} stops
          </span>
        </div>
        <div className="trip-map-body">
          <StoreMap
            stores={mappedStores}
            routeStart={routeStart}
            routeStores={orderedTripStores}
            selectedStore={selectedStore}
            onSelect={onSelectStore}
          />
        </div>
      </div>

      <div className="map-side-rail">
        <StoreDetailDrawer
          selectedStore={selectedStore}
          activeTab={activeTab}
          setActiveTab={setActiveTab}
          onBuyerSaved={onBuyerSaved}
          onContactLogSaved={onContactLogSaved}
          routeAction={selectedStore ? {
            disabled: !canAddSelectedStore,
            isAdded: isSelectedStoreInRoute,
            onAdd: () => {
              if (selectedStoreKey) {
                handleAddRouteStore(selectedStoreKey);
              }
            },
            onRemove: () => {
              if (selectedStoreKey) {
                handleRemoveRouteStore(selectedStoreKey);
              }
            }
          } : undefined}
        />

        <aside className="panel trip-planner-panel">
          <div className="panel-header">
            <h3>Trip Planner</h3>
            <span className="table-meta">{routeStart.label || "Custom start"}</span>
          </div>

          <div className="trip-section">
            <div className="trip-summary">
              <div className="metric">
                <div className="metric-label">Stops</div>
                <div className="metric-value">{orderedTripStores.length.toLocaleString()}</div>
              </div>
              <div className="metric">
                <div className="metric-label">Est. Miles</div>
                <div className="metric-value">{Math.round(estimatedMiles).toLocaleString()}</div>
              </div>
              <div className="metric">
                <div className="metric-label">Balaclava</div>
                <div className="metric-value">{formatUsd(tripBalaclava)}</div>
              </div>
              <div className="metric">
                <div className="metric-label">Market</div>
                <div className="metric-value">{formatUsd(tripMarket)}</div>
              </div>
            </div>
            <div className="route-settings" aria-label="Route settings">
              <div className="field">
                <label>Start location</label>
                <input
                  value={routeStart.label}
                  onChange={(event) => updateRouteStartLabel(event.target.value)}
                  placeholder="Start label"
                />
              </div>
              <div className="route-setting-grid">
                <div className="field">
                  <label>Start latitude</label>
                  <input
                    type="number"
                    step="0.0001"
                    value={routeStart.latitude}
                    onChange={(event) => updateRouteStartCoordinate("latitude", event.currentTarget.valueAsNumber)}
                  />
                </div>
                <div className="field">
                  <label>Start longitude</label>
                  <input
                    type="number"
                    step="0.0001"
                    value={routeStart.longitude}
                    onChange={(event) => updateRouteStartCoordinate("longitude", event.currentTarget.valueAsNumber)}
                  />
                </div>
              </div>
              <div className="route-setting-grid">
                <div className="field">
                  <label>Off-route miles</label>
                  <input
                    type="number"
                    min={1}
                    max={75}
                    step={1}
                    value={maxOffRouteMiles}
                    onChange={(event) => {
                      const value = event.currentTarget.valueAsNumber;
                      if (Number.isFinite(value)) {
                        setMaxOffRouteMiles(Math.max(1, Math.min(75, value)));
                      }
                    }}
                  />
                </div>
                <div className="field">
                  <label>Suggested stops</label>
                  <input
                    type="number"
                    min={0}
                    max={25}
                    step={1}
                    value={maxSuggestedStops}
                    onChange={(event) => {
                      const value = event.currentTarget.valueAsNumber;
                      if (Number.isFinite(value)) {
                        setMaxSuggestedStops(Math.max(0, Math.min(25, Math.round(value))));
                      }
                    }}
                  />
                </div>
              </div>
            </div>
            <div className="trip-actions">
              {routeUrl ? (
                <a className="primary-button" href={routeUrl} rel="noreferrer" target="_blank">
                  <ExternalLink size={15} /> Launch Route
                </a>
              ) : (
                <button className="primary-button" disabled type="button">
                  <ExternalLink size={15} /> Launch Route
                </button>
              )}
              <button
                className="secondary-button"
                disabled={!routeSuggestions.length}
                onClick={() => onAddWaypoints(routeSuggestions.map((suggestion) => storeKey(suggestion.store)))}
                type="button"
              >
                <ListPlus size={15} /> Add Suggested
              </button>
              <button
                className="secondary-button"
                disabled={!orderedTripStores.length}
                onClick={onClearTrip}
                type="button"
              >
                <Trash2 size={15} /> Clear
              </button>
            </div>
            {orderedTripStores.length > GOOGLE_MAPS_ROUTE_STOP_LIMIT ? (
              <div className="trip-note">
                Maps launch includes the first {launchStopCount.toLocaleString()} of{" "}
                {orderedTripStores.length.toLocaleString()} planned stops.
              </div>
            ) : null}
          </div>

          <div className="trip-section">
            <div className="trip-section-header">
              <h4>Route</h4>
              <span>{orderedTripStores.length.toLocaleString()}</span>
            </div>
            <ol className="trip-stop-list">
              {orderedTripStores.map((store, index) => {
                const isDestination = Boolean(routeDestinationKey && storeKey(store) === routeDestinationKey);
                return (
                  <li
                    className={selectedStoreKey === storeKey(store) ? "trip-stop-row is-selected" : "trip-stop-row"}
                    key={storeKey(store)}
                  >
                    <span className="trip-stop-index">{isDestination ? "D" : index + 1}</span>
                    <button className="trip-store-button" onClick={() => onSelectStore(storeKey(store))} type="button">
                      <strong>{store.storeName}</strong>
                      <span className="trip-store-meta">
                        <BrandPlacementDots store={store} />
                        <span className="trip-store-subtext">
                          {isDestination ? "Destination · " : ""}{store.city || "No city"} ·{" "}
                          {formatUsd(store.marketSalesLastMonth)} market
                        </span>
                      </span>
                    </button>
                    <button
                      aria-label={`Remove ${store.storeName} from trip`}
                      className="icon-button"
                      onClick={() => onRemoveStore(storeKey(store))}
                      type="button"
                    >
                      <X size={15} />
                    </button>
                  </li>
                );
              })}
              {!orderedTripStores.length ? (
                <li className="trip-empty">No stops selected.</li>
              ) : null}
            </ol>
          </div>

          <div className="trip-section">
            <div className="trip-section-header">
              <h4>Suggested Stops</h4>
              <span>{routeSuggestions.length.toLocaleString()}</span>
            </div>
            <div className="trip-candidate-list">
              {routeSuggestions.map((suggestion) => (
                <div
                  className={selectedStoreKey === storeKey(suggestion.store) ? "trip-candidate-row is-selected" : "trip-candidate-row"}
                  key={storeKey(suggestion.store)}
                >
                  <button
                    className="trip-store-button"
                    onClick={() => onSelectStore(storeKey(suggestion.store))}
                    type="button"
                  >
                    <strong>{suggestion.store.storeName}</strong>
                    <span className="trip-store-meta">
                      <BrandPlacementDots store={suggestion.store} />
                      <span className="trip-store-subtext">
                        {suggestion.store.city || "No city"} · {suggestion.offRouteMiles.toFixed(1)} mi off route
                      </span>
                    </span>
                  </button>
                  <button
                    aria-label={`Add ${suggestion.store.storeName} as a route stop`}
                    className="icon-button"
                    onClick={() => handleAddRouteStore(storeKey(suggestion.store))}
                    type="button"
                  >
                    <Plus size={15} />
                  </button>
                </div>
              ))}
              {!destinationStore ? (
                <div className="trip-empty">Set a destination to see stops along the way.</div>
              ) : null}
              {destinationStore && !routeSuggestions.length ? (
                <div className="trip-empty">No suggestions inside the current route corridor.</div>
              ) : null}
            </div>
          </div>

          <div className="trip-section">
            <div className="trip-section-header">
              <h4>Candidates</h4>
              <span>
                {candidateStores.length.toLocaleString()} of {unselectedCandidateStores.length.toLocaleString()}
              </span>
            </div>
            <div className="trip-candidate-list">
              {candidateStores.map((store) => (
                <div
                  className={selectedStoreKey === storeKey(store) ? "trip-candidate-row is-selected" : "trip-candidate-row"}
                  key={storeKey(store)}
                >
                  <button className="trip-store-button" onClick={() => onSelectStore(storeKey(store))} type="button">
                    <strong>{store.storeName}</strong>
                    <span className="trip-store-meta">
                      <BrandPlacementDots store={store} />
                      <span className="trip-store-subtext">
                        {store.city || "No city"} · {formatUsd(store.marketSalesLastMonth)} market
                      </span>
                    </span>
                  </button>
                  <button
                    aria-label={`Add ${store.storeName} to route`}
                    className="icon-button"
                    onClick={() => handleAddRouteStore(storeKey(store))}
                    type="button"
                  >
                    <Plus size={15} />
                  </button>
                </div>
              ))}
              {!candidateStores.length ? (
                <div className="trip-empty">No mapped candidates.</div>
              ) : null}
            </div>
          </div>
        </aside>
      </div>
    </section>
  );
}

function StoreDetailDrawer({
  selectedStore,
  activeTab,
  setActiveTab,
  onBuyerSaved,
  onContactLogSaved,
  routeAction
}: {
  selectedStore?: StoreRollup;
  activeTab: DetailTab;
  setActiveTab: (tab: DetailTab) => void;
  onBuyerSaved: (storeId: string, buyer: BuyerContactPatch) => void;
  onContactLogSaved: (storeId: string, contactLog: ContactLogPatch) => void;
  routeAction?: {
    disabled: boolean;
    isAdded: boolean;
    onAdd: () => void;
    onRemove: () => void;
  };
}) {
  return (
    <aside className="panel store-detail">
      <div className="detail-title">
        <h3>
          <span>{selectedStore?.storeName ?? "Select a store"}</span>
        </h3>
        {!selectedStore ? <span className="caption">Store detail drawer</span> : null}
      </div>
      {selectedStore && routeAction ? (
        <div className="detail-actions">
          <button
            className={routeAction.isAdded ? "secondary-button" : "primary-button"}
            disabled={routeAction.disabled}
            onClick={routeAction.isAdded ? routeAction.onRemove : routeAction.onAdd}
            type="button"
          >
            {routeAction.isAdded ? <X size={15} /> : <Plus size={15} />}
            {routeAction.isAdded ? "Remove from route" : "Add to route"}
          </button>
        </div>
      ) : null}
      {selectedStore ? <StoreDetailSummary store={selectedStore} /> : null}
      {selectedStore ? (
        <div className="metrics detail-metrics">
          <LatestMonthStat store={selectedStore} />
          <DetailStat label="Market Sales" value={formatUsd(selectedStore.marketSalesLastMonth)} />
        </div>
      ) : null}
      <div className="detail-tabs" role="tablist" aria-label="Store detail sections">
        {detailTabs.map((tab) => (
          <button
            key={tab.id}
            className={activeTab === tab.id ? "active" : ""}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {selectedStore ? (
        <StoreDetailContent
          activeTab={activeTab}
          store={selectedStore}
          onBuyerSaved={onBuyerSaved}
          onContactLogSaved={onContactLogSaved}
        />
      ) : null}
    </aside>
  );
}

function StoreDetailContent({
  activeTab,
  store,
  onBuyerSaved,
  onContactLogSaved
}: {
  activeTab: DetailTab;
  store: StoreRollup;
  onBuyerSaved: (storeId: string, buyer: BuyerContactPatch) => void;
  onContactLogSaved: (storeId: string, contactLog: ContactLogPatch) => void;
}) {
  if (activeTab === "orders") {
    return (
      <div className="detail-stack">
        <div className="metrics detail-metrics">
          <DetailStat label="Orders" value={store.orders.toLocaleString()} />
          <DetailStat label="Brand Revenue" value={formatUsd(store.brandRevenue)} />
        </div>
        <div className="detail-list">
          <DetailRow label="Last order" value={formatDate(store.lastOrderAt)} />
          <DetailRow label="Order #" value={store.lastOrderNumber} />
          <DetailRow label="K. Savage last order" value={formatDate(store.kSavageLastOrderAt)} />
          <DetailRow label="K. Savage history" value={formatUsd(store.kSavageHistoricalRevenue)} />
          <DetailRow label="Mayfield active" value={formatUsd(store.mayfieldActiveRevenue)} />
          <DetailRow label="Leisure Land active" value={formatUsd(store.leisureLandActiveRevenue)} />
        </div>
      </div>
    );
  }

  if (activeTab === "buyer") {
    return <BuyerEditor store={store} onSaved={onBuyerSaved} />;
  }

  if (activeTab === "history") {
    return (
      <div className="detail-stack">
        <div className="detail-tabs">
          <CheckState active={store.hasContactEver} label="Any log" />
          <CheckState active={store.hasContactThisMonth} label="This month" />
          <CheckState active={store.hasContactThisWeek} label="This week" />
        </div>
        <div className="detail-list">
          <DetailRow label="Log count" value={store.contactLogCount.toLocaleString()} />
          <DetailRow label="Last contact" value={formatDate(store.lastContactDate)} />
          <DetailRow label="Method" value={store.lastContactMethod} />
          <DetailRow label="Person" value={store.lastContactPerson} />
        </div>
        {store.lastContactNotes ? <p className="detail-note">{store.lastContactNotes}</p> : null}
      </div>
    );
  }

  if (activeTab === "samples") {
    return (
      <div className="detail-stack">
        <div className="metrics detail-metrics">
          <DetailStat label="Sample Drops" value={store.sampleDropCount.toLocaleString()} />
          <DetailStat label="Latest Drop" value={formatDate(store.latestSampleDate)} />
        </div>
        <div className="detail-list">
          <DetailRow label="Brand" value={store.latestSampleBrand} />
          <DetailRow label="Product" value={store.latestSampleProduct} />
        </div>
      </div>
    );
  }

  return (
    <ContactLogForm store={store} onSaved={onContactLogSaved} />
  );
}

export function StoreDashboard({ snapshot }: StoreDashboardProps) {
  const [stores, setStores] = useState(snapshot.stores);
  const [storeQuery, setStoreQuery] = useState("");
  const [draftFilters, setDraftFilters] = useState<StoreFilters>(defaultStoreFilters);
  const [appliedFilters, setAppliedFilters] = useState<StoreFilters>(defaultStoreFilters);
  const [activeView, setActiveView] = useState<ViewMode>("stores");
  const [activeTab, setActiveTab] = useState<DetailTab>("contact");
  const [sortKey, setSortKey] = useState<SortKey>("storeRevenue");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [tripStoreKeys, setTripStoreKeys] = useState<string[]>([]);
  const [routeDestinationKey, setRouteDestinationKey] = useState("");
  const [selectedStoreKey, setSelectedStoreKey] = useState(() => (
    snapshot.stores[0] ? storeKey(snapshot.stores[0]) : ""
  ));
  const normalizedStoreQuery = storeQuery.trim().toLowerCase();
  const filteredStores = useMemo(() => {
    const searchedStores = normalizedStoreQuery
      ? stores.filter((store) => (
        store.storeName.toLowerCase().includes(normalizedStoreQuery) ||
        store.license.toLowerCase().includes(normalizedStoreQuery) ||
        store.licenseKey.toLowerCase().includes(normalizedStoreQuery)
      ))
      : stores;

    return applyStoreFilters(searchedStores, appliedFilters);
  }, [appliedFilters, normalizedStoreQuery, stores]);
  const sortedStores = useMemo(
    () => sortStores(filteredStores, sortKey, sortDirection),
    [filteredStores, sortDirection, sortKey]
  );
  const metrics = useMemo(() => summarizeStores(sortedStores), [sortedStores]);
  const mappedStoreCount = useMemo(() => sortedStores.filter(hasStoreCoordinates).length, [sortedStores]);
  const tripEligibleKeySet = useMemo(() => new Set(
    sortedStores.filter(hasStoreCoordinates).map(storeKey)
  ), [sortedStores]);
  const regionOptions = useMemo(() => (
    [...new Set(stores.map((store) => textSortValue(store.county)).filter(Boolean))]
      .sort((left, right) => left.localeCompare(right))
  ), [stores]);
  const draftBrandFilters = normalizeBrandFilters(draftFilters.brand);
  const appliedBrandFilters = normalizeBrandFilters(appliedFilters.brand);
  const draftActiveFilterCount = countActiveFilters(draftFilters);
  const appliedActiveFilterCount = countActiveFilters(appliedFilters);
  const selectedStore = sortedStores.find((store) => storeKey(store) === selectedStoreKey) || sortedStores[0];
  const rowMetaBase = normalizedStoreQuery
    ? `${sortedStores.length.toLocaleString()} of ${stores.length.toLocaleString()} rows`
    : `${sortedStores.length.toLocaleString()} rows`;
  const rowMeta = appliedActiveFilterCount
    ? `${rowMetaBase} · ${appliedActiveFilterCount} filter${appliedActiveFilterCount === 1 ? "" : "s"}`
    : rowMetaBase;

  useEffect(() => {
    setStores(snapshot.stores);
  }, [snapshot.stores]);

  useEffect(() => {
    if (!sortedStores.length) {
      setSelectedStoreKey("");
      return;
    }
    if (!sortedStores.some((store) => storeKey(store) === selectedStoreKey)) {
      setSelectedStoreKey(storeKey(sortedStores[0]));
    }
  }, [selectedStoreKey, sortedStores]);

  useEffect(() => {
    setTripStoreKeys((currentKeys) => {
      const nextKeys = currentKeys.filter((key) => tripEligibleKeySet.has(key));
      return nextKeys.length === currentKeys.length ? currentKeys : nextKeys;
    });
  }, [tripEligibleKeySet]);

  useEffect(() => {
    if (routeDestinationKey && !tripEligibleKeySet.has(routeDestinationKey)) {
      setRouteDestinationKey("");
    }
  }, [routeDestinationKey, tripEligibleKeySet]);

  function handleBuyerSaved(storeId: string, buyer: BuyerContactPatch) {
    setStores((currentStores) => currentStores.map((store) => (
      store.storeId === storeId ? { ...store, ...buyer } : store
    )));
  }

  function handleContactLogSaved(storeId: string, contactLog: ContactLogPatch) {
    setStores((currentStores) => currentStores.map((store) => (
      store.storeId === storeId
        ? {
          ...store,
          contactLogCount: store.contactLogCount + 1,
          hasContactEver: true,
          hasContactThisMonth: store.hasContactThisMonth || isContactThisMonth(contactLog.dateContacted),
          hasContactThisWeek: store.hasContactThisWeek || isContactThisWeek(contactLog.dateContacted),
          lastContactDate: contactLog.dateContacted || contactLog.savedAt,
          lastContactMethod: contactLog.contactMethod,
          lastContactPerson: contactLog.personContacted,
          lastContactNotes: contactLog.notes
        }
        : store
    )));
  }

  const handleStoreSelect = useCallback((nextStoreKey: string) => {
    setSelectedStoreKey(nextStoreKey);
  }, []);

  const handleSetRouteDestination = useCallback((nextStoreKey: string) => {
    setTripStoreKeys((currentKeys) => (
      currentKeys.includes(nextStoreKey) ? currentKeys : [...currentKeys, nextStoreKey]
    ));
    setRouteDestinationKey(nextStoreKey);
  }, []);

  const handleAddRouteWaypoint = useCallback((nextStoreKey: string) => {
    setTripStoreKeys((currentKeys) => (
      currentKeys.includes(nextStoreKey) ? currentKeys : [...currentKeys, nextStoreKey]
    ));
  }, []);

  const handleAddRouteWaypoints = useCallback((nextStoreKeys: string[]) => {
    setTripStoreKeys((currentKeys) => {
      const keySet = new Set(currentKeys);
      nextStoreKeys.forEach((key) => keySet.add(key));
      return [...keySet];
    });
  }, []);

  const handleRemoveTripStore = useCallback((nextStoreKey: string) => {
    setTripStoreKeys((currentKeys) => currentKeys.filter((key) => key !== nextStoreKey));
    setRouteDestinationKey((currentKey) => (currentKey === nextStoreKey ? "" : currentKey));
  }, []);

  const handleClearTrip = useCallback(() => {
    setTripStoreKeys([]);
    setRouteDestinationKey("");
  }, []);

  function updateDraftFilter<K extends keyof StoreFilters>(key: K, value: StoreFilters[K]) {
    setDraftFilters((currentFilters) => ({
      ...currentFilters,
      [key]: value
    }));
  }

  function toggleDraftBrand(brand: BrandFilter, checked: boolean) {
    setDraftFilters((currentFilters) => {
      const currentBrands = normalizeBrandFilters(currentFilters.brand);
      const nextBrands = checked
        ? [...currentBrands, brand].filter((value, index, values) => values.indexOf(value) === index)
        : currentBrands.filter((value) => value !== brand);

      return {
        ...currentFilters,
        brand: nextBrands
      };
    });
  }

  function handleApplyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAppliedFilters(draftFilters);
  }

  function handleSort(nextSortKey: SortKey) {
    if (nextSortKey === sortKey) {
      setSortDirection((currentDirection) => (currentDirection === "asc" ? "desc" : "asc"));
      return;
    }

    setSortKey(nextSortKey);
    setSortDirection(
      nextSortKey === "balaclava" || nextSortKey === "storeRevenue" || nextSortKey === "priority" || nextSortKey === "log" ? "desc" : "asc"
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-logo" src="/logo.png" alt="RODYO" />
          <span>Balaclava Brands</span>
        </div>
        <nav className="nav" aria-label="Main navigation">
          <button
            className={activeView === "stores" ? "active" : ""}
            type="button"
            onClick={() => setActiveView("stores")}
          >
            Stores
          </button>
          <button
            className={activeView === "map" ? "active" : ""}
            type="button"
            onClick={() => setActiveView("map")}
          >
            Map
          </button>
          <button type="button">Orders</button>
          <button type="button">Goals</button>
          <button type="button">Sync</button>
        </nav>
      </aside>

      <main className="main">
        <section className="toolbar">
          <div className="toolbar-title">
            <div>
              <h2>{activeView === "map" ? "Map" : "Stores"}</h2>
              <div className="caption">
                {activeView === "map"
                  ? `${mappedStoreCount.toLocaleString()} mapped of ${sortedStores.length.toLocaleString()} filtered stores · ${tripStoreKeys.length.toLocaleString()} stops planned`
                  : snapshot.source === "demo"
                  ? "Demo shell. Connect Supabase to load live CRM data."
                  : "Live Supabase data"}
              </div>
            </div>
            <button className="primary-button" type="button" onClick={() => setActiveView("map")}>
              <MapIcon size={16} /> Launch Map
            </button>
          </div>

          <form className="filters" aria-label="Store filters" onSubmit={handleApplyFilters}>
            <div className="field store-filter-field">
              <label>Stores</label>
              <input
                type="search"
                value={storeQuery}
                onChange={(event) => setStoreQuery(event.target.value)}
                placeholder="Name or license"
              />
            </div>
            <div className="field">
              <FilterLabel active={appliedFilters.balaclavaSales !== "all"}>Balaclava Sales</FilterLabel>
              <select
                value={draftFilters.balaclavaSales}
                onChange={(event) => (
                  updateDraftFilter("balaclavaSales", event.target.value as BalaclavaSalesFilter)
                )}
              >
                <option value="all">Any range</option>
                <option value="1000">$1k+</option>
                <option value="5000">$5k+</option>
              </select>
            </div>
            <div className="field">
              <FilterLabel active={appliedFilters.storeRevenue !== "all"}>Store Revenue</FilterLabel>
              <select
                value={draftFilters.storeRevenue}
                onChange={(event) => (
                  updateDraftFilter("storeRevenue", event.target.value as StoreRevenueFilter)
                )}
              >
                <option value="all">Any range</option>
                <option value="300">$300+</option>
                <option value="50000">$50k+</option>
                <option value="100000">$100k+</option>
              </select>
            </div>
            <div className="field">
              <FilterLabel active={appliedBrandFilters.length > 0}>Brand</FilterLabel>
              <details className="multi-select">
                <summary className="multi-select-trigger">{brandFilterLabel(draftBrandFilters)}</summary>
                <div className="multi-select-menu">
                  <label className="check-option">
                    <input
                      checked={!draftBrandFilters.length}
                      onChange={() => updateDraftFilter("brand", [])}
                      type="checkbox"
                    />
                    <span className="check-option-label">All brands</span>
                    <span aria-hidden="true" className="filter-brand-dots">
                      {TERRITORY_BRANDS.map((brand) => (
                        <span
                          className="filter-brand-dot"
                          key={brand}
                          style={{ background: BRAND_DOT_COLORS[brand] ?? "var(--muted)" }}
                        />
                      ))}
                    </span>
                  </label>
                  {TERRITORY_BRANDS.map((brand) => (
                    <label className="check-option" key={brand}>
                      <input
                        checked={draftBrandFilters.includes(brand)}
                        onChange={(event) => toggleDraftBrand(brand, event.target.checked)}
                        type="checkbox"
                      />
                      <span className="check-option-label">{brand}</span>
                      <span
                        aria-hidden="true"
                        className="filter-brand-dot"
                        style={{ background: BRAND_DOT_COLORS[brand] ?? "var(--muted)" }}
                      />
                    </label>
                  ))}
                </div>
              </details>
            </div>
            <div className="field">
              <FilterLabel active={appliedFilters.pareto !== "all"}>Pareto</FilterLabel>
              <select
                value={draftFilters.pareto}
                onChange={(event) => updateDraftFilter("pareto", event.target.value as ParetoFilter)}
              >
                <option value="all">All stores</option>
                <option value="top30">Top 30</option>
                <option value="eighty">80% revenue set</option>
              </select>
            </div>
            <div className="field">
              <FilterLabel active={appliedFilters.priority !== "all"}>Priority</FilterLabel>
              <select
                value={draftFilters.priority}
                onChange={(event) => updateDraftFilter("priority", event.target.value as PriorityFilter)}
              >
                <option value="all">All priorities</option>
                <option value="lapsed">Lapsed</option>
                <option value="open-lane">Open lane</option>
              </select>
            </div>
            <div className="field">
              <FilterLabel active={appliedFilters.region !== "all"}>Region</FilterLabel>
              <select
                value={draftFilters.region}
                onChange={(event) => updateDraftFilter("region", event.target.value)}
              >
                <option value="all">All regions</option>
                {regionOptions.map((region) => (
                  <option key={region} value={region}>
                    {region.replace(/\b\w/g, (letter) => letter.toUpperCase())}
                  </option>
                ))}
              </select>
            </div>
            <button className="primary-button" type="submit">
              <SlidersHorizontal size={16} /> Apply{draftActiveFilterCount ? ` (${draftActiveFilterCount})` : ""}
            </button>
          </form>
        </section>

        <section className="metrics">
          <div className="metric">
            <div className="metric-label">Retailers</div>
            <div className="metric-value">{metrics.totalRetailers.toLocaleString()}</div>
          </div>
          <div className="metric">
            <div className="metric-label">Mapped</div>
            <div className="metric-value">{metrics.mappedStores.toLocaleString()}</div>
          </div>
          <div className="metric">
            <div className="metric-label">Lapsed Priority</div>
            <div className="metric-value">{metrics.lapsedPriority.toLocaleString()}</div>
          </div>
          <div className="metric">
            <div className="metric-label">Open Lane</div>
            <div className="metric-value">{metrics.openLanePriority.toLocaleString()}</div>
          </div>
          <div className="metric">
            <div className="metric-label">Pitch Mayfield</div>
            <div className="metric-value">{metrics.pitchMayfield.toLocaleString()}</div>
          </div>
        </section>

        {activeView === "stores" ? (
          <section className="content-grid">
            <div className="panel">
              <div className="panel-header">
                <h3>Filtered Stores</h3>
                <span className="table-meta">{rowMeta}</span>
              </div>
              <table className="store-table">
                <thead>
                  <tr>
                    {sortableColumns.map((column) => {
                      const isActive = column.key === sortKey;
                      return (
                        <th
                          key={column.key}
                          aria-sort={isActive ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}
                          style={column.width ? { width: column.width } : undefined}
                        >
                          <button
                            className="sort-header"
                            type="button"
                            onClick={() => handleSort(column.key)}
                          >
                            <span>{column.label}</span>
                            <span aria-hidden="true" className="sort-indicator">
                              {isActive ? (sortDirection === "asc" ? "↑" : "↓") : "↕"}
                            </span>
                          </button>
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {sortedStores.map((store) => (
                    <tr
                      key={storeKey(store)}
                      className={selectedStore && storeKey(store) === storeKey(selectedStore) ? "is-selected" : ""}
                      tabIndex={0}
                      onClick={() => handleStoreSelect(storeKey(store))}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          handleStoreSelect(storeKey(store));
                        }
                      }}
                    >
                      <td>
                        <div className="store-name">{store.storeName}</div>
                        <div className="store-subtext">
                          {store.license} · {store.city || "No city"} {store.zip || ""}
                        </div>
                      </td>
                      <td>
                        <BrandPlacementDots store={store} />
                      </td>
                      <td className="priority-cell">
                        <PriorityDot store={store} />
                      </td>
                      <td>{formatUsd(store.latestMonthRevenue)}</td>
                      <td>{formatUsd(store.marketSalesLastMonth)}</td>
                      <td>{store.territoryRep || "-"}</td>
                      <td>{store.hasContactEver ? "✅" : ""}</td>
                    </tr>
                  ))}
                  {!sortedStores.length ? (
                    <tr>
                      <td colSpan={7}>No stores match that search.</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>

            <StoreDetailDrawer
              selectedStore={selectedStore}
              activeTab={activeTab}
              setActiveTab={setActiveTab}
              onBuyerSaved={handleBuyerSaved}
              onContactLogSaved={handleContactLogSaved}
            />
          </section>
        ) : (
          <TripPlanner
            stores={sortedStores}
            selectedStore={selectedStore}
            activeTab={activeTab}
            setActiveTab={setActiveTab}
            routeDestinationKey={routeDestinationKey}
            tripStoreKeys={tripStoreKeys}
            onAddWaypoint={handleAddRouteWaypoint}
            onAddWaypoints={handleAddRouteWaypoints}
            onRemoveStore={handleRemoveTripStore}
            onClearTrip={handleClearTrip}
            onSetDestination={handleSetRouteDestination}
            onSelectStore={handleStoreSelect}
            onBuyerSaved={handleBuyerSaved}
            onContactLogSaved={handleContactLogSaved}
          />
        )}
      </main>
    </div>
  );
}
