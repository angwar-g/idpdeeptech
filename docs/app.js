let allNodes = [];
let allEdges = [];
let network = null;
let sourceSelect = null;
let actorSelect = null;
let yearSelect = null;
let pendingFilterTimer = null;
let pendingPreparingTimer = null;
const searchableSelects = [];

const FULL_NETWORK_SUMMARY = "Showing the largest connected network without smaller outlier components.";
const HELIX_TYPES = [
  { label: "Government", className: "government" },
  { label: "Industry", className: "industry" },
  { label: "Academia", className: "academia" },
  { label: "Intermediary", className: "intermediary" },
  { label: "Civil Society", className: "civil" },
  { label: "Unknown", className: "unknown" }
];

Promise.all([
  fetch("data/combined_nodes.json").then(assertOk).then(r => r.json()),
  fetch("data/combined_edges.json").then(assertOk).then(r => r.json())
])
  .then(([nodes, edges]) => {
    allNodes = nodes;
    allEdges = edges;

    document.getElementById("totalNodeMetric").textContent = nodes.length.toLocaleString();
    document.getElementById("totalEdgeMetric").textContent = edges.length.toLocaleString();

    sourceSelect = createSearchableMultiSelect({
      rootId: "sourceSelect",
      inputId: "sourceSearch",
      chipsId: "selectedSources",
      optionsId: "sourceOptions",
      emptyText: "No matching sources",
      defaultText: "Type to search sources",
      onChange() {
        updateActorFilterOptions();
        scheduleApplyFilters();
      }
    });

    actorSelect = createSearchableMultiSelect({
      rootId: "actorSelect",
      inputId: "actorSearch",
      chipsId: "selectedActors",
      optionsId: "actorOptions",
      emptyText: "No matching actors",
      defaultText: "Type to search actors",
      onChange: scheduleApplyFilters,
      onChipClick: focusActorFromFilterChip
    });

    yearSelect = createSearchableMultiSelect({
      rootId: "yearSelect",
      inputId: "yearSearch",
      chipsId: "selectedYears",
      optionsId: "yearOptions",
      emptyText: "No matching years",
      defaultText: "Type to search years",
      onChange: scheduleApplyFilters
    });

    populateFilters(nodes, edges);

    document.getElementById("resetBtn").addEventListener("click", resetFilters);

    resetFilters();
  })
  .catch(error => {
    console.error("Failed to load graph data:", error);
    setLoading(false);
    document.getElementById("details").innerHTML =
      `<b>Error loading graph data</b><br>${escapeHtml(error.message)}`;
  });

document.getElementById("details").addEventListener("click", event => {
  const toggle = event.target.closest(".source-toggle");
  if (!toggle) return;

  const sourceList = toggle.closest(".source-lines");
  if (!sourceList) return;

  const expanded = sourceList.classList.toggle("expanded");
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.textContent = expanded
    ? "Show less"
    : `+ ${toggle.dataset.moreCount} more`;
});

function assertOk(response) {
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}: ${response.url}`);
  }
  return response;
}

// Year handling: edges now carry `first_seen` / `last_seen` (from news article
// dates joined at merge time). For edges without dates, fall back to scanning
// the source_documents list for a YYYY pattern (covers PDFs like japan25.pdf).
function yearsForEdge(edge) {
  const years = new Set();
  if (edge.first_seen) years.add(edge.first_seen.slice(0, 4));
  if (edge.last_seen) years.add(edge.last_seen.slice(0, 4));
  // Per-occurrence dates (in case the edge spans multiple years).
  (edge.occurrences || []).forEach(occ => {
    if (occ.source_date) years.add(occ.source_date.slice(0, 4));
  });
  // Fallback: extract year from source document filenames (japan25.pdf -> 2025).
  if (years.size === 0) {
    (edge.source_documents || []).forEach(sd => {
      const m = String(sd).match(/(?:^|[^0-9])(\d{2})(?:\.pdf$|\D|$)/);
      if (m) years.add("20" + m[1]);
      const m4 = String(sd).match(/20\d{2}/);
      if (m4) years.add(m4[0]);
    });
  }
  return years;
}

function populateFilters(nodes, edges) {
  // Collect source websites/documents across all edges and nodes. URLs are
  // grouped by hostname so different pages of one website appear together.
  const allSources = new Map();
  edges.forEach(edge => {
    (edge.source_documents || []).forEach(sd => {
      addSourceOption(allSources, sd);
    });
  });
  // Also include actor sources (an actor may appear in a doc with no edges).
  nodes.forEach(node => {
    (node.source_documents || []).forEach(sd => {
      addSourceOption(allSources, sd);
    });
  });

  const sources = [...allSources.values()]
    .sort((a, b) => a.label.localeCompare(b.label));

  const allYears = new Set();
  edges.forEach(edge => yearsForEdge(edge).forEach(y => allYears.add(y)));
  const years = [...allYears]
    .sort()
    .map(year => ({ value: year, label: year }));

  sourceSelect.setOptions(sources);

  updateActorFilterOptions();
  yearSelect.setOptions(years);
}

function updateActorFilterOptions() {
  const selectedSources = sourceSelect
    ? sourceSelect.getSelectedValues()
    : new Set();
  actorSelect.setOptions(getActorOptionsForSources(selectedSources), true);
}

function getActorOptionsForSources(selectedSources) {
  let actorKeys = null;

  if (selectedSources.size > 0) {
    actorKeys = new Set();

    allEdges.forEach(edge => {
      const edgeMatchesSource = (edge.occurrences || []).some(occ =>
        selectedSources.has(getSourceGroupKey(occ.source_document))
      ) || (edge.source_documents || []).some(source =>
        selectedSources.has(getSourceGroupKey(source))
      );

      if (!edgeMatchesSource) return;
      if (edge.source_actor_key) actorKeys.add(edge.source_actor_key);
      if (edge.target_actor_key) actorKeys.add(edge.target_actor_key);
    });

    allNodes.forEach(node => {
      const nodeMatchesSource = (node.source_documents || []).some(source =>
        selectedSources.has(getSourceGroupKey(source))
      );

      if (nodeMatchesSource && node.canonical_actor_key) {
        actorKeys.add(node.canonical_actor_key);
      }
    });
  }

  const actors = allNodes
    .filter(node => node.canonical_actor_key && node.entity)
    .filter(node => !actorKeys || actorKeys.has(node.canonical_actor_key))
    .map(node => ({
      value: node.canonical_actor_key,
      label: cleanActorLabel(node.entity)
    }))
    .sort((a, b) => a.label.localeCompare(b.label));

  return deduplicateOptions(actors);
}

function cleanActorLabel(label) {
  let value = String(label || "").trim();

  value = value.replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1");
  value = value.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1");
  value = value.replace(/<[^>]+>/g, " ");

  try {
    const url = new URL(value);
    const hostname = normalizeHostname(url.hostname);
    const path = url.pathname.replace(/^\/+|\/+$/g, "");
    value = path ? `${hostname} / ${path}` : hostname;
  } catch {
    // Ordinary names with parentheses, such as "AFRL (AFRL)", are kept intact.
  }

  return value.replace(/\s+/g, " ").trim();
}

function deduplicateOptions(options) {
  const seen = new Set();
  return options.filter(option => {
    const key = `${option.value}::${option.label.toLowerCase()}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function createSearchableMultiSelect(config) {
  const root = document.getElementById(config.rootId);
  const input = document.getElementById(config.inputId);
  const chips = document.getElementById(config.chipsId);
  const menu = document.getElementById(config.optionsId);
  const caret = root.querySelector(".select-caret");

  let options = [];
  const selected = new Map();
  let lastSelectedValue = null;

  const api = {
    setOptions(newOptions, pruneSelected = false) {
      options = newOptions.map(option => ({
        ...option,
        searchLabel: `${option.label} ${option.value}`.toLowerCase()
      }));

      if (pruneSelected) {
        const optionValues = new Set(options.map(option => option.value));
        selected.forEach((_, value) => {
          if (!optionValues.has(value)) selected.delete(value);
        });
        if (!selected.has(lastSelectedValue)) {
          lastSelectedValue = [...selected.keys()].at(-1) || null;
        }
        renderChips();
      }

      renderMenu();
    },

    getSelectedValues() {
      return new Set(selected.keys());
    },

    getLastSelectedValue() {
      return selected.has(lastSelectedValue) ? lastSelectedValue : null;
    },

    setLastSelectedValue(value) {
      if (selected.has(value)) {
        lastSelectedValue = value;
      }
    },

    clear() {
      selected.clear();
      lastSelectedValue = null;
      input.value = "";
      renderChips();
      closeMenu();
    }
  };

  input.addEventListener("input", renderMenu);
  input.addEventListener("focus", openMenu);

  input.addEventListener("keydown", event => {
    if (event.key === "Backspace" && !input.value && selected.size) {
      const removedValue = [...selected.keys()].at(-1);
      selected.delete(removedValue);
      if (lastSelectedValue === removedValue) {
        lastSelectedValue = [...selected.keys()].at(-1) || null;
      }
      renderChips();
      config.onChange();
    }

    if (event.key === "Escape") {
      closeMenu();
    }
  });

  root.addEventListener("click", event => {
    if (event.target === root) input.focus();
  });

  if (caret) {
    caret.setAttribute("aria-expanded", "false");
    caret.addEventListener("mousedown", event => {
      event.preventDefault();
    });

    caret.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      const wasOpen = root.classList.contains("open");
      input.focus();
      wasOpen ? closeMenu() : openMenu();
    });
  }

  document.addEventListener("click", event => {
    if (!root.contains(event.target)) closeMenu();
  });

  function openMenu() {
    searchableSelects.forEach(select => {
      if (select.root !== root) select.closeMenu();
    });

    root.classList.add("open");
    if (caret) caret.setAttribute("aria-expanded", "true");
    renderMenu();
  }

  function closeMenu() {
    root.classList.remove("open");
    if (caret) caret.setAttribute("aria-expanded", "false");
  }

  function renderChips() {
    chips.innerHTML = "";

    selected.forEach((label, value) => {
      const chip = document.createElement("span");
      chip.className = "select-chip";
      chip.tabIndex = config.onChipClick ? 0 : -1;
      chip.append(document.createTextNode(label));

      if (config.onChipClick) {
        chip.addEventListener("click", () => {
          lastSelectedValue = value;
          config.onChipClick(value);
        });

        chip.addEventListener("keydown", event => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          lastSelectedValue = value;
          config.onChipClick(value);
        });
      }

      const removeButton = document.createElement("button");
      removeButton.type = "button";
      removeButton.className = "select-chip-remove";
      removeButton.setAttribute("aria-label", `Remove ${label}`);
      removeButton.textContent = "×";

      removeButton.addEventListener("click", event => {
        event.stopPropagation();
        selected.delete(value);
        if (lastSelectedValue === value) {
          lastSelectedValue = [...selected.keys()].at(-1) || null;
        }
        renderChips();
        renderMenu();
        config.onChange();
      });

      chip.appendChild(removeButton);
      chips.appendChild(chip);
    });
  }

  function renderMenu() {
    const query = input.value.trim().toLowerCase();

    const matches = options
      .filter(option => !selected.has(option.value))
      .filter(option => !query || option.searchLabel.includes(query));

    menu.innerHTML = "";
    menu.appendChild(renderActions(matches));

    if (!matches.length) {
      const empty = document.createElement("div");
      empty.className = "search-option empty";
      empty.textContent = query ? config.emptyText : config.defaultText;
      menu.appendChild(empty);
      return;
    }

    matches.forEach(option => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "search-option";
      button.setAttribute("role", "option");
      button.textContent = option.label;

      button.addEventListener("click", event => {
        event.preventDefault();
        selected.set(option.value, option.label);
        lastSelectedValue = option.value;
        input.value = "";
        renderChips();
        renderMenu();
        config.onChange();
      });

      menu.appendChild(button);
    });
  }

  function renderActions(matches) {
    const row = document.createElement("div");
    row.className = "search-actions";

    const selectAll = document.createElement("button");
    selectAll.type = "button";
    selectAll.textContent = input.value.trim() ? "Select matches" : "Select all";

    selectAll.addEventListener("click", event => {
      event.preventDefault();

      const items = matches.length ? matches : options;

      items.forEach(option => {
        selected.set(option.value, option.label);
        lastSelectedValue = option.value;
      });

      input.value = "";
      renderChips();
      renderMenu();
      config.onChange();
    });

    row.append(selectAll);
    return row;
  }

  searchableSelects.push({ root, closeMenu });

  return api;
}

function addSourceOption(sourceMap, source) {
  if (!source) return;

  const value = getSourceGroupKey(source);
  if (sourceMap.has(value)) return;

  sourceMap.set(value, {
    value,
    label: getSourceGroupLabel(source)
  });
}

function getSourceGroupKey(source) {
  const value = String(source || "").trim();
  if (!value) return "";

  try {
    const url = new URL(value);
    return `site:${normalizeHostname(url.hostname)}`;
  } catch {
    return `doc:${value}`;
  }
}

function getSourceGroupLabel(source) {
  const value = String(source || "").trim();
  if (!value) return "Unknown source";

  try {
    const url = new URL(value);
    return normalizeHostname(url.hostname);
  } catch {
    return value;
  }
}

function normalizeHostname(hostname) {
  return String(hostname || "")
    .trim()
    .toLowerCase()
    .replace(/^www\./, "");
}

function scheduleApplyFilters() {
  if (pendingFilterTimer !== null) {
    clearTimeout(pendingFilterTimer);
  }
  if (pendingPreparingTimer !== null) {
    clearTimeout(pendingPreparingTimer);
  }

  setLoading(true, 10, "Preparing graph data...");

  pendingPreparingTimer = setTimeout(() => {
    pendingPreparingTimer = null;
    setLoading(true, 22, "Preparing graph data...");
  }, 90);

  pendingFilterTimer = setTimeout(() => {
    pendingFilterTimer = null;
    if (pendingPreparingTimer !== null) {
      clearTimeout(pendingPreparingTimer);
      pendingPreparingTimer = null;
    }
    applyFilters();
  }, 260);
}

function applyFilters() {
  const selectedSources = sourceSelect.getSelectedValues();
  const selectedActors = actorSelect.getSelectedValues();
  const selectedYears = yearSelect.getSelectedValues();

  const noFilters =
    selectedSources.size === 0 &&
    selectedActors.size === 0 &&
    selectedYears.size === 0;

  if (noFilters) {
    showFullNetwork();
    resetDetailsPanel();
    return;
  }

  // Edge passes if AT LEAST ONE of its occurrences satisfies all active filters.
  // We also keep the matching occurrences only so tooltips show only what fits.
  const sourceYearFilteredEdges = [];
  allEdges.forEach(edge => {
    const matchingOccurrences = (edge.occurrences || []).filter(occ => {
      const docOk = selectedSources.size === 0 ||
                    selectedSources.has(getSourceGroupKey(occ.source_document));
      const yearOk = selectedYears.size === 0 ||
                     occurrenceYearsMatch(occ, edge, selectedYears);
      return docOk && yearOk;
    });
    if (matchingOccurrences.length === 0 &&
        (selectedSources.size > 0 || selectedYears.size > 0)) {
      return;
    }

    // Make a shallow copy of the edge with only the matching occurrences,
    // so tooltip text reflects what was actually selected.
    sourceYearFilteredEdges.push({
      ...edge,
      occurrences: selectedSources.size === 0 && selectedYears.size === 0
        ? edge.occurrences
        : matchingOccurrences
    });
  });

  const actorFilteredGraph = selectedActors.size > 0
    ? getSelectedActorComponentGraph(sourceYearFilteredEdges, selectedActors)
    : { edges: sourceYearFilteredEdges, isolatedActorKeys: new Set() };
  const filteredEdges = actorFilteredGraph.edges;
  const visibleActorKeys = new Set();
  filteredEdges.forEach(edge => {
    if (edge.source_actor_key) visibleActorKeys.add(edge.source_actor_key);
    if (edge.target_actor_key) visibleActorKeys.add(edge.target_actor_key);
  });
  actorFilteredGraph.isolatedActorKeys.forEach(key => visibleActorKeys.add(key));

  // Also include nodes whose own source_documents intersect the source filter
  // (an actor mentioned in a doc but with no surviving edges).
  if (selectedSources.size > 0 && selectedActors.size === 0) {
    allNodes.forEach(node => {
      const nodeDocs = node.source_documents || [];
      if (nodeDocs.some(d => selectedSources.has(getSourceGroupKey(d)))) {
        if (node.canonical_actor_key) visibleActorKeys.add(node.canonical_actor_key);
      }
    });
  }

  const filteredNodes = getNodesForActorKeys(allNodes, visibleActorKeys, filteredEdges);

  drawGraph(filteredNodes, filteredEdges, {
    isInitialView: false,
    usePhysics: false,
    selectedActorKeys: selectedActors,
    activeActorKey: actorSelect.getLastSelectedValue()
  });

  if (selectedActors.size === 0) {
    resetDetailsPanel();
  }

  updateFilterSummary(
    filteredNodes.length,
    filteredEdges.length,
    filteredNodes,
    filteredNodes.length > 1500
      ? "Showing filtered graph with static layout."
      : "Showing filtered graph with static layout."
  );
}

function occurrenceYearsMatch(occ, edge, selectedYears) {
  if (occ.source_date) {
    return selectedYears.has(occ.source_date.slice(0, 4));
  }
  // No explicit date on this occurrence -- fall back to whole-edge year set
  // (covers PDFs with a year-stamped filename).
  const fallbackYears = yearsForEdge(edge);
  for (const y of fallbackYears) {
    if (selectedYears.has(y)) return true;
  }
  return false;
}

function getSelectedActorComponentGraph(edges, selectedActors) {
  const adjacency = new Map();

  edges.forEach(edge => {
    const source = edge.source_actor_key;
    const target = edge.target_actor_key;
    if (!source || !target) return;

    if (!adjacency.has(source)) adjacency.set(source, new Set());
    if (!adjacency.has(target)) adjacency.set(target, new Set());
    adjacency.get(source).add(target);
    adjacency.get(target).add(source);
  });

  const componentActorKeys = new Set();
  const isolatedActorKeys = new Set();

  selectedActors.forEach(actorKey => {
    if (!adjacency.has(actorKey)) {
      isolatedActorKeys.add(actorKey);
      return;
    }

    const stack = [actorKey];
    componentActorKeys.add(actorKey);

    while (stack.length) {
      const current = stack.pop();
      (adjacency.get(current) || new Set()).forEach(next => {
        if (componentActorKeys.has(next)) return;
        componentActorKeys.add(next);
        stack.push(next);
      });
    }
  });

  const componentEdges = edges.filter(edge =>
    componentActorKeys.has(edge.source_actor_key) &&
    componentActorKeys.has(edge.target_actor_key)
  );

  return {
    edges: componentEdges,
    isolatedActorKeys
  };
}

function resetFilters() {
  sourceSelect.clear();
  updateActorFilterOptions();
  actorSelect.clear();
  yearSelect.clear();

  scheduleApplyFilters();
  resetDetailsPanel();
}

function resetDetailsPanel() {
  document.getElementById("details").innerHTML = "Click a node or edge to inspect it.";
}

function showFullNetwork() {
  const connectedGraph = getConnectedGraph(allNodes, allEdges);

  drawGraph(connectedGraph.nodes, connectedGraph.edges, {
    isFullNetwork: true,
    usePhysics: false
  });

  updateFilterSummary(
    connectedGraph.nodes.length,
    connectedGraph.edges.length,
    connectedGraph.nodes,
    FULL_NETWORK_SUMMARY
  );
}

function getConnectedGraph(nodes, edges) {
  const connectedEdges = edges.filter(edge =>
    edge.source_actor_key && edge.target_actor_key
  );
  const adjacency = new Map();

  connectedEdges.forEach(edge => {
    if (!adjacency.has(edge.source_actor_key)) {
      adjacency.set(edge.source_actor_key, new Set());
    }
    if (!adjacency.has(edge.target_actor_key)) {
      adjacency.set(edge.target_actor_key, new Set());
    }

    adjacency.get(edge.source_actor_key).add(edge.target_actor_key);
    adjacency.get(edge.target_actor_key).add(edge.source_actor_key);
  });

  const largestComponent = getConnectedComponents([...adjacency.keys()], adjacency)
    .sort((a, b) => b.length - a.length)[0] || [];
  const largestActorKeys = new Set(largestComponent);
  const largestEdges = connectedEdges.filter(edge =>
    largestActorKeys.has(edge.source_actor_key) &&
    largestActorKeys.has(edge.target_actor_key)
  );

  return {
    nodes: getNodesForActorKeys(nodes, largestActorKeys, largestEdges),
    edges: largestEdges
  };
}

function getNodesForActorKeys(nodes, actorKeys, edges) {
  const nodeByKey = new Map(
    nodes
      .filter(node => node.canonical_actor_key)
      .map(node => [node.canonical_actor_key, node])
  );
  const inferredNodes = new Map();

  edges.forEach(edge => {
    if (!edge.source_actor_key || !edge.target_actor_key) return;

    addInferredEndpointNode(
      inferredNodes,
      nodeByKey,
      edge.source_actor_key,
      edge.source_actor,
      edge
    );
    addInferredEndpointNode(
      inferredNodes,
      nodeByKey,
      edge.target_actor_key,
      edge.target_actor,
      edge
    );
  });

  return [
    ...nodes.filter(node => actorKeys.has(node.canonical_actor_key)),
    ...inferredNodes.values()
  ];
}

function addInferredEndpointNode(inferredNodes, nodeByKey, key, label, edge) {
  if (!key || nodeByKey.has(key) || inferredNodes.has(key)) return;

  inferredNodes.set(key, {
    canonical_actor_key: key,
    entity: label || key,
    category: "Unknown",
    helix: "Unknown",
    sphere: "Unknown",
    r_and_d: "",
    source_documents: edge.source_documents || []
  });
}

function updateFilterSummary(nodeCount, edgeCount, visibleNodes = [], message = "Fetching graph data and loading network.") {
  document.getElementById("visibleNodeCount").textContent = nodeCount.toLocaleString();
  document.getElementById("visibleEdgeCount").textContent = edgeCount.toLocaleString();
  document.getElementById("filterSummary").textContent = message;
  renderHelixLegend(visibleNodes);
}

function renderHelixLegend(nodes) {
  const legend = document.getElementById("helixLegend");
  if (!legend) return;

  const counts = new Map(HELIX_TYPES.map(type => [type.label, 0]));

  nodes.forEach(node => {
    const helix = HELIX_TYPES.some(type => type.label === node.helix)
      ? node.helix
      : "Unknown";
    counts.set(helix, (counts.get(helix) || 0) + 1);
  });

  legend.innerHTML = "";

  HELIX_TYPES.forEach(type => {
    const item = document.createElement("div");
    item.className = "topbar-legend-item";

    const dot = document.createElement("span");
    dot.className = `legend-dot ${type.className}`;

    const label = document.createElement("span");
    label.className = "topbar-legend-label";
    label.textContent = type.label;

    const count = document.createElement("strong");
    count.textContent = (counts.get(type.label) || 0).toLocaleString();

    item.append(dot, label, count);
    legend.appendChild(item);
  });
}

function drawGraph(nodes, edges, settings = {}) {
  const {
    isInitialView = false,
    isFullNetwork = false,
    usePhysics = true,
    selectedActorKeys = new Set(),
    activeActorKey = null
  } = settings;
  const useStaticLayout = isFullNetwork || !usePhysics;
  const compactComponents = useStaticLayout && !isFullNetwork && nodes.length <= 500;

  setLoading(
    true,
    8,
    `Building ${nodes.length.toLocaleString()} actors and ${edges.length.toLocaleString()} edges...`
  );

  const staticPositions = useStaticLayout
    ? getStaticGraphPositions(nodes, edges, { compactComponents })
    : new Map();
  const nodeMap = new Map();

  nodes.forEach((node, index) => {
    if (!node.canonical_actor_key) return;

    const isSelectedActor = selectedActorKeys.has(node.canonical_actor_key);
    const staticPosition = useStaticLayout
      ? staticPositions.get(node.canonical_actor_key) || getStaticNodePosition(node.canonical_actor_key, index, nodes.length)
      : {};
    const nodeSize = isFullNetwork ? getNodeSize(node) * 0.62 : getNodeSize(node);

    const sourceCount = (node.source_documents || []).length;
    const dateRange = node.earliest_date
      ? `${node.earliest_date}${node.latest_date && node.latest_date !== node.earliest_date ? ` – ${node.latest_date}` : ""}`
      : "";

    nodeMap.set(node.canonical_actor_key, {
      id: node.canonical_actor_key,
      label: formatActorDisplayName(node.entity || node.canonical_actor_key),
      ...staticPosition,
      title: createNodeTooltipText(node, sourceCount, dateRange),
      color: {
        background: getHelixColor(node.helix),
        border: isSelectedActor ? "#ffffff" : "rgba(255,255,255,0.72)",
        highlight: {
          background: getHelixColor(node.helix),
          border: "#ffffff"
        }
      },
      borderWidth: isSelectedActor ? 4 : (isFullNetwork ? 0.75 : 1),
      shadow: isSelectedActor
        ? {
            enabled: true,
            color: "rgba(143, 199, 255, 0.95)",
            size: 22,
            x: 0,
            y: 0
          }
        : undefined,
      shape: "dot",
      size: isSelectedActor ? nodeSize * 1.55 : nodeSize,
      font: {
        color: "#dcecff",
        size: isSelectedActor ? 16 : (isFullNetwork ? 11 : 13),
        face: "Inter, Arial",
        strokeWidth: 3,
        strokeColor: "#06101f"
      },
      raw: node
    });
  });

  const showEdgeLabels = !isFullNetwork && edges.length <= 120;
  const visEdges = [];

  edges.forEach((edge, index) => {
    if (!edge.source_actor_key || !edge.target_actor_key) return;

    if (!nodeMap.has(edge.source_actor_key)) {
      nodeMap.set(
        edge.source_actor_key,
        fallbackNode(edge.source_actor_key, edge.source_actor, useStaticLayout, nodeMap.size, nodes.length)
      );
    }

    if (!nodeMap.has(edge.target_actor_key)) {
      nodeMap.set(
        edge.target_actor_key,
        fallbackNode(edge.target_actor_key, edge.target_actor, useStaticLayout, nodeMap.size, nodes.length)
      );
    }

    const occurrences = edge.occurrences || [];
    const firstOcc = occurrences[0] || {};

    // Arrow only for directional relations. Symmetric ones render as a plain line.
    const directional = edge.directional === true;

    visEdges.push({
      id: `edge-${index}`,
      from: edge.source_actor_key,
      to: edge.target_actor_key,
      label: showEdgeLabels ? formatRelationLabel(edge.relation_label || "") : "",
      title: createEdgeTooltipText(edge),
      arrows: {
        to: {
          enabled: directional,
          scaleFactor: 0.65
        }
      },
      color: {
        color: isFullNetwork
          ? "rgba(151, 180, 218, 0.30)"
          : "rgba(151, 180, 218, 0.42)",
        highlight: "#9fd2ff",
        hover: "#9fd2ff"
      },
      // Slightly thicker line for edges with many occurrences (visual signal
      // of how well-attested a relation is).
      width: isFullNetwork
        ? Math.min(1.8, 0.5 + Math.log2(occurrences.length + 1) * 0.25)
        : Math.min(3.5, 1.0 + Math.log2(occurrences.length + 1) * 0.6),
      smooth: {
        enabled: !useStaticLayout,
        type: "dynamic"
      },
      font: {
        color: "#cfe4ff",
        size: isFullNetwork ? 7 : 10,
        strokeWidth: isFullNetwork ? 2 : 4,
        strokeColor: "#06101f",
        align: "middle"
      },
      raw: edge
    });
  });

  const container = document.getElementById("network");

  if (!container) {
    console.error("No #network container found");
    setLoading(false);
    return;
  }

  const data = {
    nodes: new vis.DataSet([...nodeMap.values()]),
    edges: new vis.DataSet(visEdges)
  };

  const options = {
    autoResize: true,

    layout: {
      improvedLayout: !useStaticLayout
    },

    physics: usePhysics
      ? {
          enabled: true,
          stabilization: {
            enabled: true,
            iterations: isFullNetwork ? 70 : (isInitialView ? 180 : 120),
            updateInterval: 20
          },
          barnesHut: {
            gravitationalConstant: isFullNetwork ? -1800 : -6200,
            centralGravity: isFullNetwork ? 0.05 : 0.16,
            springLength: isFullNetwork ? 85 : 165,
            springConstant: isFullNetwork ? 0.01 : 0.035,
            damping: isFullNetwork ? 0.35 : 0.12,
            avoidOverlap: isFullNetwork ? 0.03 : 0.18
          }
        }
      : {
          enabled: false,
          stabilization: false
        },

    nodes: {
      shadow: {
        enabled: !isFullNetwork,
        color: "rgba(0,0,0,0.35)",
        size: 8,
        x: 1,
        y: 2
      }
    },

    edges: {
      selectionWidth: 2,
      hoverWidth: 1.5
    },

    interaction: {
      hover: true,
      tooltipDelay: 120,
      navigationButtons: false,
      keyboard: true,
      multiselect: false,
      dragNodes: true,
      hideEdgesOnDrag: false,
      hideEdgesOnZoom: false
    }
  };

  if (network) {
    network.destroy();
  }

  network = new vis.Network(container, data, options);

  if (usePhysics) {
    network.on("stabilizationProgress", params => {
      const progress = params.total
        ? Math.round((params.iterations / params.total) * 100)
        : 50;

      setLoading(true, progress, `Laying out network... ${Math.min(progress, 100)}%`);
    });

    network.once("stabilizationIterationsDone", () => {
      freezeNetworkLayout();

      focusSelectedActors(selectedActorKeys, activeActorKey);

      setLoading(false);
    });
  } else {
    setTimeout(() => {
      freezeNetworkLayout();

      focusSelectedActors(selectedActorKeys, activeActorKey);

      setLoading(false);
    }, 80);
  }

  network.on("click", params => {
    if (params.nodes.length > 0) {
      const node = data.nodes.get(params.nodes[0]);
      renderNodeDetails(node);
      return;
    }

    if (params.edges.length > 0) {
      const edge = data.edges.get(params.edges[0]);
      renderEdgeDetails(edge);
      return;
    }

    resetDetailsPanel();
  });
}

function formatOccurrenceList(occurrences) {
  if (!occurrences.length) return "<i>No occurrences listed</i>";

  return occurrences.slice(0, 5).map(occ => {
    const date = occ.source_date ? `<b>${escapeHtml(occ.source_date)}</b> · ` : "";
    return `
      <div style="margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid rgba(255,255,255,0.08);">
        ${date}${escapeHtml(occ.source_document || "")}<br>
        ${occ.interaction_phrase ? `<i>${escapeHtml(occ.interaction_phrase)}</i><br>` : ""}
        ${escapeHtml(occ.occurrence_sentence || "")}
      </div>
    `;
  }).join("") + (occurrences.length > 5 ? `<i>+ ${occurrences.length - 5} more</i>` : "");
}

function freezeNetworkLayout() {
  if (!network) return;

  network.stopSimulation();

  try {
    network.storePositions();
  } catch (error) {
    console.warn("Unable to store static node positions:", error);
  }

  network.setOptions({
    physics: {
      enabled: false,
      stabilization: false
    },
    interaction: {
      dragNodes: true,
      hideEdgesOnDrag: false
    }
  });
}

function focusSelectedActors(selectedActorKeys, activeActorKey = null) {
  if (!network) return;

  if (!selectedActorKeys.size) {
    network.fit({
      animation: {
        duration: 650,
        easingFunction: "easeInOutQuad"
      }
    });
    return;
  }

  const selectedIds = [...selectedActorKeys].filter(id =>
    network.body?.data?.nodes?.get(id)
  );
  const activeId = activeActorKey && selectedIds.includes(activeActorKey)
    ? activeActorKey
    : selectedIds.at(-1);

  if (activeActorKey && activeId) {
    network.selectNodes([activeId], true);
    const activeNode = network.body.data.nodes.get(activeId);
    if (activeNode) renderNodeDetails(activeNode);
    network.focus(activeId, {
      scale: 1.15,
      animation: {
        duration: 650,
        easingFunction: "easeInOutQuad"
      }
    });
    return;
  }

  if (activeId) {
    network.selectNodes([activeId], true);
  }

  network.fit({
    nodes: selectedIds.length ? selectedIds : undefined,
    animation: {
      duration: 650,
      easingFunction: "easeInOutQuad"
    }
  });
}

function focusActorFromFilterChip(actorKey) {
  if (!network || !actorKey) return;

  actorSelect.setLastSelectedValue(actorKey);

  const node = network.body?.data?.nodes?.get(actorKey);
  if (!node) return;

  network.selectNodes([actorKey], true);
  renderNodeDetails(node);
  network.focus(actorKey, {
    scale: 1.15,
    animation: {
      duration: 650,
      easingFunction: "easeInOutQuad"
    }
  });
}

function renderNodeDetails(node) {
  const raw = node.raw || {};
  const dateRange = raw.earliest_date
    ? `${raw.earliest_date}${raw.latest_date && raw.latest_date !== raw.earliest_date ? ` – ${raw.latest_date}` : ""}`
    : "";

  document.getElementById("details").innerHTML = `
    <span class="detail-title"><b>${escapeHtml(node.label)}</b></span><br><br>
    <b>Category:</b> ${escapeHtml(formatTitleCase(raw.category || "Unknown"))}<br>
    <b>Helix:</b> ${escapeHtml(raw.helix || "Unknown")}<br>
    <b>Sphere:</b> ${escapeHtml(raw.sphere || "Unknown")}<br>
    <b>R&amp;D:</b> ${escapeHtml(formatRnDValue(raw.r_and_d))}<br>
    ${dateRange ? `<b>Date range:</b> ${escapeHtml(dateRange)}<br>` : ""}<br>
    <b>Sources (${(raw.source_documents || []).length}):</b><br>
    ${formatSourceList(raw.source_documents || [])}
  `;
}

function renderEdgeDetails(edge) {
  const raw = edge.raw || {};

  document.getElementById("details").innerHTML = `
    <span class="detail-title">
      <b>${escapeHtml(formatActorDisplayName(raw.source_actor || raw.source_actor_key || ""))}</b>
      ${raw.directional ? "→" : "↔"}
      <b>${escapeHtml(formatActorDisplayName(raw.target_actor || raw.target_actor_key || ""))}</b>
    </span><br><br>
    <b>Label:</b> ${escapeHtml(formatRelationLabel(raw.relation_label || "Interaction"))}<br>
    <b>Functional space:</b> ${escapeHtml(getEdgeFunctionalSpace(raw))}<br>
    <b>Direction:</b> ${raw.directional ? "Directional" : "Symmetric"}<br>
    ${raw.first_seen ? `<b>First seen:</b> ${escapeHtml(raw.first_seen)}<br>` : ""}
    ${raw.last_seen && raw.last_seen !== raw.first_seen ? `<b>Last seen:</b> ${escapeHtml(raw.last_seen)}<br>` : ""}
    <br>
    <b>Sources (${(raw.source_documents || []).length}):</b><br>
    ${formatSourceList(raw.source_documents || [])}
  `;
}

function getStaticGraphPositions(nodes, edges, options = {}) {
  const { compactComponents = false } = options;
  const nodeKeys = nodes
    .map(node => node.canonical_actor_key)
    .filter(Boolean);
  const nodeKeySet = new Set(nodeKeys);
  const adjacency = new Map(nodeKeys.map(key => [key, []]));
  const edgePairs = [];

  edges.forEach(edge => {
    const source = edge.source_actor_key;
    const target = edge.target_actor_key;

    if (!nodeKeySet.has(source) || !nodeKeySet.has(target) || source === target) return;

    adjacency.get(source).push(target);
    adjacency.get(target).push(source);
    edgePairs.push([source, target]);
  });

  const components = getConnectedComponents(nodeKeys, adjacency)
    .sort((a, b) => b.length - a.length);
  const componentEdges = new Map();

  components.forEach((component, index) => {
    component.forEach(key => componentEdges.set(key, index));
  });

  const positions = new Map();
  const connectedComponents = components.filter(component => component.length > 1);
  const singletonComponents = components.filter(component => component.length === 1);

  connectedComponents.forEach((component, index) => {
    const center = getComponentCenter(index, component.length, compactComponents);
    const componentKeys = new Set(component);
    const localEdges = edgePairs.filter(([source, target]) =>
      componentEdges.get(source) === componentEdges.get(target) &&
      componentKeys.has(source)
    );
    const localPositions = layoutComponent(component, localEdges);

    localPositions.forEach((position, key) => {
      positions.set(key, {
        x: position.x + center.x,
        y: position.y + center.y
      });
    });
  });

  singletonComponents.forEach((component, index) => {
    const key = component[0];
    const position = compactComponents
      ? getCompactComponentCenter(connectedComponents.length + index, 1)
      : getSingletonPosition(key, index, singletonComponents.length);
    positions.set(key, position);
  });

  return positions;
}

function getConnectedComponents(nodeKeys, adjacency) {
  const seen = new Set();
  const components = [];

  nodeKeys.forEach(key => {
    if (seen.has(key)) return;

    const component = [];
    const stack = [key];
    seen.add(key);

    while (stack.length) {
      const current = stack.pop();
      component.push(current);

      (adjacency.get(current) || []).forEach(next => {
        if (seen.has(next)) return;
        seen.add(next);
        stack.push(next);
      });
    }

    components.push(component);
  });

  return components;
}

function getComponentCenter(index, size, compactComponents = false) {
  if (compactComponents) return getCompactComponentCenter(index, size);
  if (index === 0) return { x: 0, y: 0 };

  const random = createSeededRandom(`component:${index}:${size}`);
  const side = index % 2 === 0 ? 1 : -1;
  const row = Math.floor((index - 1) / 2);
  const x = side * (5200 + (row % 4) * 700 + random() * 360);
  const y = -4200 + Math.floor(row / 4) * 1150 + (random() - 0.5) * 360;

  return { x, y };
}

function getCompactComponentCenter(index, size) {
  if (index === 0) return { x: 0, y: 0 };

  const random = createSeededRandom(`compact-component:${index}:${size}`);
  const ringIndex = index - 1;
  const angle = ringIndex * 2.399963229728653;
  const ring = Math.floor(ringIndex / 8);
  const radius = 720 + ring * 520 + Math.min(360, Math.sqrt(size) * 34);

  return {
    x: Math.cos(angle) * radius + (random() - 0.5) * 90,
    y: Math.sin(angle) * radius + (random() - 0.5) * 90
  };
}

function layoutComponent(component, edges) {
  const positions = new Map();
  const velocities = new Map();
  const size = component.length;
  const radius = Math.max(140, Math.sqrt(size) * (size > 600 ? 150 : 78));

  component.forEach((key, index) => {
    const random = createSeededRandom(`${key}:component`);
    const angle = (index / size) * Math.PI * 2 + random() * 0.6;
    const distance = Math.sqrt(random()) * radius;

    positions.set(key, {
      x: Math.cos(angle) * distance,
      y: Math.sin(angle) * distance
    });
    velocities.set(key, { x: 0, y: 0 });
  });

  if (size <= 2) {
    component.forEach((key, index) => {
      positions.set(key, {
        x: (index - 0.5) * 130,
        y: 0
      });
    });
    return positions;
  }

  const iterations = size > 600 ? 120 : 90;
  const idealLength = Math.max(95, Math.min(280, 58 + Math.sqrt(size) * (size > 600 ? 7 : 5)));

  for (let i = 0; i < iterations; i += 1) {
    applyRepulsion(component, positions, velocities, size);

    edges.forEach(([source, target]) => {
      const sourcePosition = positions.get(source);
      const targetPosition = positions.get(target);
      const dx = targetPosition.x - sourcePosition.x;
      const dy = targetPosition.y - sourcePosition.y;
      const distance = Math.max(0.01, Math.hypot(dx, dy));
      const force = (distance - idealLength) * (size > 600 ? 0.0032 : 0.0045);
      const fx = (dx / distance) * force;
      const fy = (dy / distance) * force;

      velocities.get(source).x += fx;
      velocities.get(source).y += fy;
      velocities.get(target).x -= fx;
      velocities.get(target).y -= fy;
    });

    component.forEach(key => {
      const position = positions.get(key);
      const velocity = velocities.get(key);

      velocity.x += -position.x * (size > 600 ? 0.00022 : 0.0008);
      velocity.y += -position.y * (size > 600 ? 0.00022 : 0.0008);
      position.x += velocity.x;
      position.y += velocity.y;
      velocity.x *= 0.72;
      velocity.y *= 0.72;
    });
  }

  return positions;
}

function applyRepulsion(component, positions, velocities, size) {
  if (size > 1400) return;

  const strength = size > 600 ? 155 : 42;

  for (let i = 0; i < component.length; i += 1) {
    const source = component[i];
    const sourcePosition = positions.get(source);

    for (let j = i + 1; j < component.length; j += 1) {
      const target = component[j];
      const targetPosition = positions.get(target);
      const dx = targetPosition.x - sourcePosition.x;
      const dy = targetPosition.y - sourcePosition.y;
      const distanceSquared = Math.max(80, dx * dx + dy * dy);
      const force = strength / distanceSquared;
      const fx = dx * force;
      const fy = dy * force;

      velocities.get(source).x -= fx;
      velocities.get(source).y -= fy;
      velocities.get(target).x += fx;
      velocities.get(target).y += fy;
    }
  }
}

function getSingletonPosition(key, index, totalSingletons) {
  const random = createSeededRandom(`${key}:singleton`);
  const side = index % 2 === 0 ? -1 : 1;
  const sideIndex = Math.floor(index / 2);
  const columns = 18;
  const spacing = 245;
  const col = sideIndex % columns;
  const row = Math.floor(sideIndex / columns);
  const rows = Math.ceil((totalSingletons / 2) / columns);
  const xBase = side * 9200;
  const xDirection = side < 0 ? -1 : 1;
  const x = xBase + xDirection * (col - columns / 2) * spacing + (random() - 0.5) * 42;
  const y = (row - rows / 2) * spacing + (random() - 0.5) * 42;

  return { x, y };
}

function getStaticNodePosition(id, index, totalNodes) {
  const spread = Math.max(2400, Math.sqrt(totalNodes) * 430);
  const random = createSeededRandom(`${id}:${index}`);
  const angle = random() * Math.PI * 2;
  const radius = Math.sqrt(random()) * spread;
  const wobbleX = (random() - 0.5) * spread * 0.08;
  const wobbleY = (random() - 0.5) * spread * 0.08;

  return {
    x: Math.cos(angle) * radius + wobbleX,
    y: Math.sin(angle) * radius + wobbleY
  };
}

function createSeededRandom(seed) {
  let state = 1779033703 ^ seed.length;

  for (let i = 0; i < seed.length; i += 1) {
    state = Math.imul(state ^ seed.charCodeAt(i), 3432918353);
    state = (state << 13) | (state >>> 19);
  }

  return function random() {
    state = Math.imul(state ^ (state >>> 16), 2246822507);
    state = Math.imul(state ^ (state >>> 13), 3266489909);
    state ^= state >>> 16;
    return (state >>> 0) / 4294967296;
  };
}

function setLoading(isLoading, progress = 0, message = "") {
  const overlay = document.getElementById("loadingOverlay");
  const bar = document.getElementById("loadingBar");
  const text = document.getElementById("loadingText");

  if (!overlay || !bar || !text) return;

  overlay.classList.toggle("hidden", !isLoading);
  bar.style.width = `${Math.max(0, Math.min(progress, 100))}%`;

  if (message) {
    text.textContent = message;
  }
}

function fallbackNode(id, label, isFullNetwork = false, index = 0, totalNodes = 1) {
  const staticPosition = isFullNetwork
    ? getStaticNodePosition(id, index, totalNodes)
    : {};
  const displayLabel = formatActorDisplayName(label || id);

  return {
    id,
    label: displayLabel,
    ...staticPosition,
    title: createTooltipText(displayLabel, [
      ["Record type", "Node inferred from edge"]
    ]),
    color: {
      background: "#9aa4b2",
      border: "rgba(255,255,255,0.65)"
    },
    shape: "dot",
    size: 10,
    font: {
      color: "#dcecff",
      size: isFullNetwork ? 0 : 12,
      face: "Inter, Arial",
      strokeWidth: isFullNetwork ? 0 : 3,
      strokeColor: "#06101f"
    }
  };
}

function createNodeTooltipText(node, sourceCount, dateRange) {
  const rows = [
    ["Category", formatTitleCase(node.category || "Unknown")],
    ["Helix", node.helix || "Unknown"],
    ["Sphere", node.sphere || "Unknown"],
    ["R&D", formatRnDValue(node.r_and_d)],
    ["Sources", sourceCount.toLocaleString()]
  ];

  if (dateRange) {
    rows.push(["Date range", dateRange]);
  }

  return createTooltipText(formatActorDisplayName(node.entity || node.canonical_actor_key), rows);
}

function createEdgeTooltipText(edge) {
  const actorPair = [
    formatActorDisplayName(edge.source_actor || edge.source_actor_key || "Unknown source"),
    formatActorDisplayName(edge.target_actor || edge.target_actor_key || "Unknown target")
  ].join(edge.directional ? " → " : " ↔ ");

  const rows = [
    ["Label", formatRelationLabel(edge.relation_label || "Interaction")],
    ["Functional space", getEdgeFunctionalSpace(edge)],
    ["Direction", edge.directional ? "Directional" : "Symmetric"]
  ];

  if (edge.first_seen) {
    rows.push(["First seen", edge.first_seen]);
  }

  if (edge.last_seen && edge.last_seen !== edge.first_seen) {
    rows.push(["Last seen", edge.last_seen]);
  }

  const sources = edge.source_documents || [];
  rows.push(["Sources", sources.length.toLocaleString()]);

  return createTooltipText(actorPair, rows);
}

function createTooltipText(title, rows) {
  const lines = [title || "Unknown actor", ""];

  rows.forEach(([label, value]) => {
    lines.push(`${label}: ${value || "Unknown"}`);
  });

  return lines.join("\n");
}

function formatRnDValue(value) {
  const normalized = String(value || "").trim().toLowerCase();

  if (normalized === "r&d") return "True";
  if (normalized === "non-r&d") return "False";
  if (normalized === "assessed") return "Assessed";

  return "Not specified";
}

function formatTitleCase(value) {
  return String(value || "")
    .split(/\s+/)
    .filter(Boolean)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function formatRelationLabel(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .split(/\s+/)
    .filter(Boolean)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function formatActorDisplayName(value) {
  const label = String(value || "").trim();

  if (/^t\|ket>?™?$/i.test(label)) return "TKET";

  return label;
}

function getEdgeFunctionalSpace(edge) {
  if (edge.functional_space) return edge.functional_space;

  const occurrence = (edge.occurrences || []).find(item => item.functional_space);
  return occurrence ? occurrence.functional_space : "Not specified";
}

function getNodeSize(node) {
  const sources = (node.source_documents || []).length;
  const mentions = Array.isArray(node.mentions) ? node.mentions.length : sources;
  return Math.max(10, Math.min(26, 9 + Math.sqrt(mentions || 1) * 1.6));
}

function getHelixColor(helix) {
  const colors = {
    "Government": "#4C78A8",
    "Industry": "#F58518",
    "Academia": "#54A24B",
    "Intermediary": "#B279A2",
    "Civil Society": "#E45756",
    "Unknown": "#9AA4B2"
  };

  return colors[helix] || "#9AA4B2";
}

function formatSourceList(sources) {
  if (!sources.length) return "None listed";

  const visibleSources = sources.slice(0, 6)
    .map(source => `<div class="source-line">${escapeHtml(source)}</div>`)
    .join("");

  const extraSources = sources.slice(6)
    .map(source => `<div class="source-line">${escapeHtml(source)}</div>`)
    .join("");

  const remainingCount = sources.length - 6;
  const remaining = remainingCount > 0
    ? `
      <div class="source-extra">${extraSources}</div>
      <button
        type="button"
        class="source-toggle"
        data-more-count="${remainingCount}"
        aria-expanded="false"
      >+ ${remainingCount} more</button>
    `
    : "";

  return `<div class="source-lines">${visibleSources}${remaining}</div>`;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
