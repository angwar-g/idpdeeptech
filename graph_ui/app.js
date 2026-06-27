let allNodes = [];
let allEdges = [];
let network = null;
let sourceSelect = null;
let actorSelect = null;
let yearSelect = null;

const FULL_NETWORK_SUMMARY = "Showing connected network without isolated actors.";
const HELIX_TYPES = [
  { label: "Government", className: "government" },
  { label: "Industry", className: "industry" },
  { label: "Academia", className: "academia" },
  { label: "Intermediary", className: "intermediary" },
  { label: "Civil Society", className: "civil" },
  { label: "Unknown", className: "unknown" }
];

Promise.all([
  fetch("../pipeline/merged_outputs/combined_nodes.json").then(assertOk).then(r => r.json()),
  fetch("../pipeline/merged_outputs/combined_edges.json").then(assertOk).then(r => r.json())
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
      onChange: applyFilters
    });

    actorSelect = createSearchableMultiSelect({
      rootId: "actorSelect",
      inputId: "actorSearch",
      chipsId: "selectedActors",
      optionsId: "actorOptions",
      emptyText: "No matching actors",
      defaultText: "Type to search actors",
      onChange: applyFilters
    });

    yearSelect = createSearchableMultiSelect({
      rootId: "yearSelect",
      inputId: "yearSearch",
      chipsId: "selectedYears",
      optionsId: "yearOptions",
      emptyText: "No matching years",
      defaultText: "Type to search years",
      onChange: applyFilters
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
  // Collect every distinct source document across all edges.
  const allSources = new Set();
  edges.forEach(edge => {
    (edge.source_documents || []).forEach(sd => {
      if (sd) allSources.add(sd);
    });
  });
  // Also include actor sources (an actor may appear in a doc with no edges).
  nodes.forEach(node => {
    (node.source_documents || []).forEach(sd => {
      if (sd) allSources.add(sd);
    });
  });

  const sources = [...allSources].sort((a, b) => a.localeCompare(b));

  const actors = nodes
    .filter(n => n.canonical_actor_key && n.entity)
    .map(n => ({
      value: n.canonical_actor_key,
      label: n.entity.trim()
    }))
    .sort((a, b) => a.label.localeCompare(b.label));

  const allYears = new Set();
  edges.forEach(edge => yearsForEdge(edge).forEach(y => allYears.add(y)));
  const years = [...allYears]
    .sort()
    .map(year => ({ value: year, label: year }));

  sourceSelect.setOptions(sources.map(doc => ({
    value: doc,
    label: doc
  })));

  actorSelect.setOptions(deduplicateOptions(actors));
  yearSelect.setOptions(years);
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

  const api = {
    setOptions(newOptions) {
      options = newOptions.map(option => ({
        ...option,
        searchLabel: `${option.label} ${option.value}`.toLowerCase()
      }));
      renderMenu();
    },

    getSelectedValues() {
      return new Set(selected.keys());
    },

    clear() {
      selected.clear();
      input.value = "";
      renderChips();
      closeMenu();
    }
  };

  input.addEventListener("input", renderMenu);
  input.addEventListener("focus", openMenu);

  input.addEventListener("keydown", event => {
    if (event.key === "Backspace" && !input.value && selected.size) {
      selected.delete([...selected.keys()].at(-1));
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
      chip.append(document.createTextNode(label));

      const removeButton = document.createElement("button");
      removeButton.type = "button";
      removeButton.className = "select-chip-remove";
      removeButton.setAttribute("aria-label", `Remove ${label}`);
      removeButton.textContent = "×";

      removeButton.addEventListener("click", event => {
        event.stopPropagation();
        selected.delete(value);
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
      .filter(option => !query || option.searchLabel.includes(query))
      .slice(0, 80);

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
      });

      input.value = "";
      renderChips();
      renderMenu();
      config.onChange();
    });

    row.append(selectAll);
    return row;
  }

  return api;
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
    return;
  }

  // Edge passes if AT LEAST ONE of its occurrences satisfies all active filters.
  // We also keep the matching occurrences only so tooltips show only what fits.
  const filteredEdges = [];
  allEdges.forEach(edge => {
    const matchingOccurrences = (edge.occurrences || []).filter(occ => {
      const docOk = selectedSources.size === 0 ||
                    selectedSources.has(occ.source_document);
      const yearOk = selectedYears.size === 0 ||
                     occurrenceYearsMatch(occ, edge, selectedYears);
      return docOk && yearOk;
    });
    if (matchingOccurrences.length === 0 &&
        (selectedSources.size > 0 || selectedYears.size > 0)) {
      return;
    }
    const actorOk = selectedActors.size === 0 ||
                    selectedActors.has(edge.source_actor_key) ||
                    selectedActors.has(edge.target_actor_key);
    if (!actorOk) return;

    // Make a shallow copy of the edge with only the matching occurrences,
    // so tooltip text reflects what was actually selected.
    filteredEdges.push({
      ...edge,
      occurrences: selectedSources.size === 0 && selectedYears.size === 0
        ? edge.occurrences
        : matchingOccurrences
    });
  });

  const visibleActorKeys = new Set();
  filteredEdges.forEach(edge => {
    if (edge.source_actor_key) visibleActorKeys.add(edge.source_actor_key);
    if (edge.target_actor_key) visibleActorKeys.add(edge.target_actor_key);
  });

  // Also include nodes whose own source_documents intersect the source filter
  // (an actor mentioned in a doc but with no surviving edges).
  if (selectedSources.size > 0) {
    allNodes.forEach(node => {
      const nodeDocs = node.source_documents || [];
      if (nodeDocs.some(d => selectedSources.has(d))) {
        if (node.canonical_actor_key) visibleActorKeys.add(node.canonical_actor_key);
      }
    });
  }
  if (selectedActors.size > 0) {
    selectedActors.forEach(k => visibleActorKeys.add(k));
  }

  const filteredNodes = allNodes.filter(node =>
    visibleActorKeys.has(node.canonical_actor_key)
  );

  drawGraph(filteredNodes, filteredEdges, {
    isInitialView: false,
    usePhysics: filteredNodes.length <= 1500
  });

  updateFilterSummary(
    filteredNodes.length,
    filteredEdges.length,
    filteredNodes,
    filteredNodes.length > 1500
      ? "Showing filtered graph with static layout for performance."
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

function resetFilters() {
  sourceSelect.clear();
  actorSelect.clear();
  yearSelect.clear();

  showFullNetwork();
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
  const connectedActorKeys = new Set();

  edges.forEach(edge => {
    if (!edge.source_actor_key || !edge.target_actor_key) return;
    connectedActorKeys.add(edge.source_actor_key);
    connectedActorKeys.add(edge.target_actor_key);
  });

  return {
    nodes: nodes.filter(node => connectedActorKeys.has(node.canonical_actor_key)),
    edges: edges.filter(edge =>
      connectedActorKeys.has(edge.source_actor_key) &&
      connectedActorKeys.has(edge.target_actor_key)
    )
  };
}

function updateFilterSummary(nodeCount, edgeCount, visibleNodes = [], message = "Showing graph.") {
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
    usePhysics = true
  } = settings;

  setLoading(
    true,
    8,
    `Building ${nodes.length.toLocaleString()} actors and ${edges.length.toLocaleString()} edges...`
  );

  const staticPositions = isFullNetwork
    ? getStaticGraphPositions(nodes, edges)
    : new Map();
  const nodeMap = new Map();

  nodes.forEach((node, index) => {
    if (!node.canonical_actor_key) return;

    const staticPosition = isFullNetwork
      ? staticPositions.get(node.canonical_actor_key) || getStaticNodePosition(node.canonical_actor_key, index, nodes.length)
      : {};

    const sourceCount = (node.source_documents || []).length;
    const dateRange = node.earliest_date
      ? `${node.earliest_date}${node.latest_date && node.latest_date !== node.earliest_date ? ` – ${node.latest_date}` : ""}`
      : "";

    nodeMap.set(node.canonical_actor_key, {
      id: node.canonical_actor_key,
      label: node.entity || node.canonical_actor_key,
      ...staticPosition,
      title: `
        <b>${escapeHtml(node.entity || node.canonical_actor_key)}</b><br>
        Helix: ${escapeHtml(node.helix || "Unknown")}<br>
        Category: ${escapeHtml(node.category || "Unknown")}<br>
        Sources: ${sourceCount}${dateRange ? `<br>Range: ${escapeHtml(dateRange)}` : ""}
      `,
      color: {
        background: getHelixColor(node.helix),
        border: "rgba(255,255,255,0.72)",
        highlight: {
          background: getHelixColor(node.helix),
          border: "#ffffff"
        }
      },
      borderWidth: isFullNetwork ? 0.75 : 1,
      shape: "dot",
      size: isFullNetwork ? getNodeSize(node) * 0.62 : getNodeSize(node),
      font: {
        color: "#dcecff",
        size: isFullNetwork ? 11 : 13,
        face: "Inter, Arial",
        strokeWidth: 3,
        strokeColor: "#06101f"
      },
      raw: node
    });
  });

  const showEdgeLabels = edges.length <= 120;
  const visEdges = [];

  edges.forEach((edge, index) => {
    if (!edge.source_actor_key || !edge.target_actor_key) return;

    if (!nodeMap.has(edge.source_actor_key)) {
      nodeMap.set(
        edge.source_actor_key,
        fallbackNode(edge.source_actor_key, edge.source_actor, isFullNetwork, nodeMap.size, nodes.length)
      );
    }

    if (!nodeMap.has(edge.target_actor_key)) {
      nodeMap.set(
        edge.target_actor_key,
        fallbackNode(edge.target_actor_key, edge.target_actor, isFullNetwork, nodeMap.size, nodes.length)
      );
    }

    const occurrences = edge.occurrences || [];
    const firstOcc = occurrences[0] || {};

    // Arrow only for directional relations. Symmetric ones render as a plain
    // line. In full-network mode we suppress arrows regardless to keep the
    // overview readable.
    const directional = edge.directional === true && !isFullNetwork;

    const sampleSentence = firstOcc.occurrence_sentence || "";
    const samplePhrase = firstOcc.interaction_phrase || "";

    visEdges.push({
      id: `edge-${index}`,
      from: edge.source_actor_key,
      to: edge.target_actor_key,
      label: showEdgeLabels ? edge.relation_label || "" : "",
      title: `
        <b>${escapeHtml(edge.relation_label || "interaction")}</b>
        ${edge.directional ? "(directional)" : "(symmetric)"}<br>
        <b>${escapeHtml(edge.source_actor || edge.source_actor_key)}</b>
          ${edge.directional ? "→" : "↔"}
        <b>${escapeHtml(edge.target_actor || edge.target_actor_key)}</b><br>
        Mentions: ${occurrences.length}
        ${edge.first_seen ? `<br>First seen: ${escapeHtml(edge.first_seen)}` : ""}
        ${edge.last_seen && edge.last_seen !== edge.first_seen ? `<br>Last seen: ${escapeHtml(edge.last_seen)}` : ""}
        ${samplePhrase ? `<br><br><b>Phrase:</b><br>${escapeHtml(samplePhrase)}` : ""}
        ${sampleSentence ? `<br><br><b>Sample evidence:</b><br>${escapeHtml(sampleSentence)}` : ""}
      `,
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
        enabled: !isFullNetwork,
        type: "dynamic"
      },
      font: {
        color: "#cfe4ff",
        size: 10,
        strokeWidth: 4,
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
      improvedLayout: !isFullNetwork
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
      hideEdgesOnZoom: isFullNetwork
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

      network.fit({
        animation: {
          duration: 650,
          easingFunction: "easeInOutQuad"
        }
      });

      setLoading(false);
    });
  } else {
    setTimeout(() => {
      freezeNetworkLayout();

      network.fit({
        animation: {
          duration: 650,
          easingFunction: "easeInOutQuad"
        }
      });

      setLoading(false);
    }, 80);
  }

  network.on("click", params => {
    if (params.nodes.length > 0) {
      const node = data.nodes.get(params.nodes[0]);
      const raw = node.raw || {};

      const dateRange = raw.earliest_date
        ? `${raw.earliest_date}${raw.latest_date && raw.latest_date !== raw.earliest_date ? ` – ${raw.latest_date}` : ""}`
        : "";

      document.getElementById("details").innerHTML = `
        <b>${escapeHtml(node.label)}</b><br><br>
        <b>Helix:</b> ${escapeHtml(raw.helix || "Unknown")}<br>
        <b>Category:</b> ${escapeHtml(raw.category || "Unknown")}<br>
        <b>Canonical key:</b> ${escapeHtml(node.id)}<br>
        ${dateRange ? `<b>Date range:</b> ${escapeHtml(dateRange)}<br>` : ""}<br>
        <b>Sources (${(raw.source_documents || []).length}):</b><br>
        ${formatSourceList(raw.source_documents || [])}
      `;

      return;
    }

    if (params.edges.length > 0) {
      const edge = data.edges.get(params.edges[0]);
      const raw = edge.raw || {};
      const occurrences = raw.occurrences || [];

      document.getElementById("details").innerHTML = `
        <b>${escapeHtml(raw.relation_label || "interaction")}</b>
        ${raw.directional ? "(directional)" : "(symmetric)"}<br><br>
        <b>${escapeHtml(raw.source_actor || raw.source_actor_key || "")}</b>
        ${raw.directional ? "→" : "↔"}
        <b>${escapeHtml(raw.target_actor || raw.target_actor_key || "")}</b><br><br>
        <b>${occurrences.length} mention${occurrences.length === 1 ? "" : "s"}</b>
        ${raw.first_seen ? `<br><i>First: ${escapeHtml(raw.first_seen)}` : ""}
        ${raw.last_seen && raw.last_seen !== raw.first_seen ? `, Last: ${escapeHtml(raw.last_seen)}</i>` : raw.first_seen ? "</i>" : ""}
        <br><br>
        ${formatOccurrenceList(occurrences)}
      `;

      return;
    }

    document.getElementById("details").innerHTML = "Click a node or edge to inspect it.";
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

function getStaticGraphPositions(nodes, edges) {
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
    const center = getComponentCenter(index, component.length);
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
    positions.set(key, getSingletonPosition(key, index, singletonComponents.length));
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

function getComponentCenter(index, size) {
  if (index === 0) return { x: 0, y: 0 };

  const random = createSeededRandom(`component:${index}:${size}`);
  const side = index % 2 === 0 ? 1 : -1;
  const row = Math.floor((index - 1) / 2);
  const x = side * (5200 + (row % 4) * 700 + random() * 360);
  const y = -4200 + Math.floor(row / 4) * 1150 + (random() - 0.5) * 360;

  return { x, y };
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

  return {
    id,
    label: label || id,
    ...staticPosition,
    title: `<b>${escapeHtml(label || id)}</b><br>Node inferred from edge`,
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

  return sources.slice(0, 6).map(source => escapeHtml(source)).join("<br>") +
    (sources.length > 6 ? `<br>+ ${sources.length - 6} more` : "");
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
