"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Check, Map as MapIcon, SlidersHorizontal } from "lucide-react";
import type { DashboardSnapshot } from "@/lib/dashboard-data";
import { TERRITORY_BRANDS, TERRITORY_MAP_COLORS, formatUsd, type StoreRollup } from "@/lib/rules";

type StoreDashboardProps = {
  snapshot: DashboardSnapshot;
};

type ViewMode = "stores" | "map";
type DetailTab = "contact" | "orders" | "buyer" | "history" | "samples";
type SortKey = "store" | "designation" | "balaclava" | "storeRevenue" | "rep" | "log";
type SortDirection = "asc" | "desc";
type BalaclavaSalesFilter = "all" | "1000" | "5000";
type StoreRevenueFilter = "all" | "300" | "50000" | "100000";
type BrandFilter = "all" | (typeof TERRITORY_BRANDS)[number];
type ParetoFilter = "all" | "top30" | "eighty";
type PriorityFilter = "all" | "lapsed" | "open-lane";
type MapLibreModule = typeof import("maplibre-gl");
type MapLibreMap = import("maplibre-gl").Map;
type MapLibreMarker = import("maplibre-gl").Marker;

type StoreFilters = {
  balaclavaSales: BalaclavaSalesFilter;
  storeRevenue: StoreRevenueFilter;
  brand: BrandFilter;
  pareto: ParetoFilter;
  priority: PriorityFilter;
  region: string;
};

type BuyerContactPatch = {
  contactName: string | null;
  phoneNumber: string | null;
  email: string | null;
};

const defaultStoreFilters: StoreFilters = {
  balaclavaSales: "all",
  storeRevenue: "all",
  brand: "all",
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
  { key: "store", label: "Store", width: "34%" },
  { key: "designation", label: "Designation" },
  { key: "balaclava", label: "Balaclava" },
  { key: "storeRevenue", label: "Store Revenue" },
  { key: "rep", label: "Rep" },
  { key: "log", label: "Log" }
];

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

function textSortValue(value?: string | null) {
  return String(value || "").trim().toLowerCase();
}

function sortValueForStore(store: StoreRollup, sortKey: SortKey) {
  if (sortKey === "store") {
    return `${textSortValue(store.storeName)} ${textSortValue(store.license)}`;
  }
  if (sortKey === "designation") {
    return textSortValue(store.mapCategory);
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

function matchesBrandFilter(store: StoreRollup, brand: BrandFilter) {
  if (brand === "K. Savage") {
    return store.kSavageActiveRevenue > 0 || store.latestMonthRevenue > 0;
  }
  if (brand === "Mayfield") {
    return store.mayfieldActiveRevenue > 0;
  }
  if (brand === "Leisure Land") {
    return store.leisureLandActiveRevenue > 0;
  }
  return true;
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

  if (filters.brand !== "all") {
    nextStores = nextStores.filter((store) => matchesBrandFilter(store, filters.brand));
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
  return Object.values(filters).filter((value) => value !== "all").length;
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

function DetailStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
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

  return (
    <div className="detail-summary">
      <div className="detail-designation">
        <span
          className="dot"
          style={{
            background: TERRITORY_MAP_COLORS[store.mapCategory] ?? "var(--muted)"
          }}
        />
        <strong>{store.mapCategory}</strong>
      </div>
      <div className="detail-list compact">
        <DetailRow label="License" value={store.license} />
        <DetailRow label="Rep" value={store.territoryRep} />
        <DetailRow label="Location" value={location} />
        <DetailRow label="Latest Balaclava" value={formatUsd(store.latestMonthRevenue)} />
        <DetailRow label="Market sales" value={formatUsd(store.marketSalesLastMonth)} />
        <DetailRow label="Orders" value={store.orders.toLocaleString()} />
        <DetailRow label="Log entries" value={store.contactLogCount.toLocaleString()} />
      </div>
    </div>
  );
}

function StoreDetailHero({ store }: { store: StoreRollup }) {
  const location = [store.city, store.state, store.zip].filter(Boolean).join(", ");

  return (
    <div className="detail-hero">
      <div className="detail-hero-grid">
        <span>License</span>
        <strong>{store.license || "-"}</strong>
        <span>Rep</span>
        <strong>{store.territoryRep || "-"}</strong>
        <span>Location</span>
        <strong>{location || "-"}</strong>
        <span>Balaclava</span>
        <strong>{formatUsd(store.latestMonthRevenue)}</strong>
        <span>Market</span>
        <strong>{formatUsd(store.marketSalesLastMonth)}</strong>
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

function createPopupContent(store: StoreRollup) {
  const container = document.createElement("div");
  container.className = "map-popup";

  const title = document.createElement("strong");
  title.textContent = store.storeName;
  container.appendChild(title);

  const license = document.createElement("span");
  license.textContent = `${store.license} · ${store.city || "No city"}`;
  container.appendChild(license);

  const category = document.createElement("span");
  category.textContent = store.mapCategory;
  container.appendChild(category);

  const revenue = document.createElement("span");
  revenue.textContent = `Balaclava ${formatUsd(store.latestMonthRevenue)} · Market ${formatUsd(store.marketSalesLastMonth)}`;
  container.appendChild(revenue);

  return container;
}

function StoreMap({
  stores,
  selectedStore,
  onSelect
}: {
  stores: StoreRollup[];
  selectedStore?: StoreRollup;
  onSelect: (storeKeyValue: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const maplibreRef = useRef<MapLibreModule | null>(null);
  const markersRef = useRef<Map<string, { marker: MapLibreMarker; element: HTMLButtonElement }>>(new Map());
  const [isMapReady, setIsMapReady] = useState(false);
  const mappedStores = useMemo(() => stores.filter(hasStoreCoordinates), [stores]);

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
      mapRef.current?.remove();
      mapRef.current = null;
      maplibreRef.current = null;
      setIsMapReady(false);
    };
  }, []);

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
      element.className = `map-marker${selectedStore && key === storeKey(selectedStore) ? " is-selected" : ""}`;
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
  }, [isMapReady, mappedStores, onSelect, selectedStore]);

  useEffect(() => {
    markersRef.current.forEach(({ element }, key) => {
      element.classList.toggle("is-selected", Boolean(selectedStore && key === storeKey(selectedStore)));
    });
  }, [selectedStore]);

  return (
    <div className="store-map">
      <div ref={containerRef} className="map-canvas" />
      {!mappedStores.length ? (
        <div className="map-empty">No filtered stores have coordinates yet.</div>
      ) : null}
    </div>
  );
}

function MapLegend({ stores }: { stores: StoreRollup[] }) {
  const mappedStores = stores.filter(hasStoreCoordinates);
  const missingCount = stores.length - mappedStores.length;
  const categoryCounts = [...mappedStores.reduce((counts, store) => {
    counts.set(store.mapCategory, (counts.get(store.mapCategory) || 0) + 1);
    return counts;
  }, new Map<string, number>())].sort((left, right) => right[1] - left[1]);

  return (
    <aside className="map-legend" aria-label="Map legend">
      <div>
        <div className="metric-label">Mapped Stores</div>
        <div className="legend-count">{mappedStores.length.toLocaleString()}</div>
        <div className="caption">of {stores.length.toLocaleString()} filtered stores</div>
      </div>
      {missingCount ? (
        <div className="legend-warning">{missingCount.toLocaleString()} missing coordinates</div>
      ) : null}
      <div className="legend-list">
        {categoryCounts.map(([category, count]) => (
          <div className="legend-row" key={category}>
            <span
              className="dot"
              style={{
                background: TERRITORY_MAP_COLORS[category] ?? "var(--muted)"
              }}
            />
            <span>{category}</span>
            <strong>{count.toLocaleString()}</strong>
          </div>
        ))}
      </div>
    </aside>
  );
}

function StoreDetailDrawer({
  selectedStore,
  activeTab,
  setActiveTab,
  onBuyerSaved
}: {
  selectedStore?: StoreRollup;
  activeTab: DetailTab;
  setActiveTab: (tab: DetailTab) => void;
  onBuyerSaved: (storeId: string, buyer: BuyerContactPatch) => void;
}) {
  return (
    <aside className="panel store-detail">
      <div className="detail-title">
        <h3>
          <span>{selectedStore?.storeName ?? "Select a store"}</span>
          {selectedStore ? (
            <small>
              {selectedStore.license || "-"} · {selectedStore.mapCategory} · Balaclava{" "}
              {formatUsd(selectedStore.latestMonthRevenue)} · Market{" "}
              {formatUsd(selectedStore.marketSalesLastMonth)}
            </small>
          ) : null}
        </h3>
        <span className="caption">
          {selectedStore ? `${selectedStore.license} · ${selectedStore.city ?? ""}` : "Store detail drawer"}
        </span>
        {selectedStore ? <StoreDetailHero store={selectedStore} /> : null}
      </div>
      {selectedStore ? <StoreDetailSummary store={selectedStore} /> : null}
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
        />
      ) : null}
    </aside>
  );
}

function StoreDetailContent({
  activeTab,
  store,
  onBuyerSaved
}: {
  activeTab: DetailTab;
  store: StoreRollup;
  onBuyerSaved: (storeId: string, buyer: BuyerContactPatch) => void;
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
    <div className="detail-stack">
      <div className="metrics detail-metrics">
        <DetailStat label="Latest Month" value={formatUsd(store.latestMonthRevenue)} />
        <DetailStat label="Market Sales" value={formatUsd(store.marketSalesLastMonth)} />
      </div>
      <div className="detail-tabs">
        <CheckState active={store.hasContactEver} label="Any log" />
        <CheckState active={store.hasContactThisMonth} label="This month" />
        <CheckState active={store.hasContactThisWeek} label="This week" />
      </div>
      <div className="form-grid">
        <div className="field">
          <label>Contact method</label>
          <select defaultValue="">
            <option value="">Select</option>
            <option>In-person</option>
            <option>Phone</option>
            <option>Email</option>
          </select>
        </div>
        <div className="field">
          <label>Initials</label>
          <select defaultValue="">
            <option value="">Select</option>
            <option>DK</option>
            <option>CH</option>
          </select>
        </div>
      </div>
      <button className="primary-button" type="button">
        Save Contact Log
      </button>
    </div>
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
  const regionOptions = useMemo(() => (
    [...new Set(stores.map((store) => textSortValue(store.county)).filter(Boolean))]
      .sort((left, right) => left.localeCompare(right))
  ), [stores]);
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

  function handleBuyerSaved(storeId: string, buyer: BuyerContactPatch) {
    setStores((currentStores) => currentStores.map((store) => (
      store.storeId === storeId ? { ...store, ...buyer } : store
    )));
  }

  const handleStoreSelect = useCallback((nextStoreKey: string) => {
    setSelectedStoreKey(nextStoreKey);
  }, []);

  function updateDraftFilter<K extends keyof StoreFilters>(key: K, value: StoreFilters[K]) {
    setDraftFilters((currentFilters) => ({
      ...currentFilters,
      [key]: value
    }));
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
      nextSortKey === "balaclava" || nextSortKey === "storeRevenue" || nextSortKey === "log" ? "desc" : "asc"
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-logo" src="/logo.png" alt="RODYO" />
          <span>Balaclava store operations</span>
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
                  ? `${mappedStoreCount.toLocaleString()} mapped of ${sortedStores.length.toLocaleString()} filtered stores`
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
              <label>Balaclava Sales</label>
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
              <label>Store Revenue</label>
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
              <label>Brand</label>
              <select
                value={draftFilters.brand}
                onChange={(event) => updateDraftFilter("brand", event.target.value as BrandFilter)}
              >
                <option value="all">All brands</option>
                {TERRITORY_BRANDS.map((brand) => (
                  <option key={brand} value={brand}>
                    {brand}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Pareto</label>
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
              <label>Priority</label>
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
              <label>Region</label>
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
                        <span className="tag">
                          <span
                            className="dot"
                            style={{
                              background: TERRITORY_MAP_COLORS[store.mapCategory] ?? "var(--muted)"
                            }}
                          />
                          {store.mapCategory}
                        </span>
                      </td>
                      <td>{formatUsd(store.latestMonthRevenue)}</td>
                      <td>{formatUsd(store.marketSalesLastMonth)}</td>
                      <td>{store.territoryRep || "-"}</td>
                      <td>{store.hasContactEver ? "✅" : ""}</td>
                    </tr>
                  ))}
                  {!sortedStores.length ? (
                    <tr>
                      <td colSpan={6}>No stores match that search.</td>
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
            />
          </section>
        ) : (
          <section className="map-layout">
            <div className="panel map-panel">
              <div className="panel-header">
                <h3>Filtered Store Map</h3>
                <span className="table-meta">
                  {mappedStoreCount.toLocaleString()} mapped · {sortedStores.length.toLocaleString()} filtered
                </span>
              </div>
              <div className="map-body">
                <StoreMap
                  stores={sortedStores}
                  selectedStore={selectedStore}
                  onSelect={handleStoreSelect}
                />
                <MapLegend stores={sortedStores} />
              </div>
            </div>

            <StoreDetailDrawer
              selectedStore={selectedStore}
              activeTab={activeTab}
              setActiveTab={setActiveTab}
              onBuyerSaved={handleBuyerSaved}
            />
          </section>
        )}
      </main>
    </div>
  );
}
