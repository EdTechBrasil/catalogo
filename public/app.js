/**
 * Catálogo Gráfica Educar — SPA
 * Stack: Vanilla JS + AG Grid Community
 */

const API_BASE = "";

// PDF.js worker
if (typeof pdfjsLib !== "undefined") {
  pdfjsLib.GlobalWorkerOptions.workerSrc =
    "https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.worker.min.js";
}

const COLUMNS = [
  "Item", "Opção", "Coleção", "Faixa etária / nível", "Título",
  "Ilustrador(es) 1", "Ilustrador(es) 2", "ISBN", "Ano de publicação",
  "Número de páginas", "Sinopse", "Preço unitário", "Material de apoio pedagógico",
];

// ── State ─────────────────────────────────────────────────────────────────────
const State = {
  records: [],         // fonte de verdade
  selectedRows: [],    // linhas marcadas no grid
  gridApi: null,
};

// ── Utils ─────────────────────────────────────────────────────────────────────

function emptyRecord() {
  return Object.fromEntries(COLUMNS.map(c => [c, ""]));
}

function renumber(records) {
  return records.map((r, i) => ({ ...r, Item: i + 1 }));
}

function showSpinner(msg = "Processando…") {
  document.getElementById("spinner").classList.add("active");
  document.getElementById("spinner-msg").textContent = msg;
}

function hideSpinner() {
  document.getElementById("spinner").classList.remove("active");
}

function toast(msg, type = "info", duration = 3500) {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.getElementById("toast-container").appendChild(el);
  setTimeout(() => el.remove(), duration);
}

function setStatus(msg) {
  document.getElementById("status-msg").textContent = msg;
}

// ── Grid ──────────────────────────────────────────────────────────────────────

const COL_DEFS = [
  {
    headerCheckboxSelection: true,
    checkboxSelection: true,
    width: 44,
    pinned: "left",
    lockPosition: true,
    suppressMovable: true,
    resizable: false,
    sortable: false,
    filter: false,
    field: "_sel",
    headerName: "",
  },
  { field: "Item",                   width: 64,  editable: false, sortable: true },
  { field: "Opção",                  width: 72,  editable: true },
  { field: "Coleção",                width: 180, editable: true },
  { field: "Faixa etária / nível",   width: 160, editable: true },
  { field: "Título",                 width: 340, editable: true },
  { field: "Ilustrador(es) 1",       width: 200, editable: true },
  { field: "Ilustrador(es) 2",       width: 200, editable: true },
  { field: "ISBN",                   width: 160, editable: true },
  { field: "Ano de publicação",      width: 120, editable: true },
  { field: "Número de páginas",      width: 110, editable: true },
  { field: "Sinopse",                width: 360, editable: true, wrapText: true, autoHeight: true },
  { field: "Preço unitário",         width: 120, editable: true },
  { field: "Material de apoio pedagógico", width: 260, editable: true },
];

function initGrid() {
  const gridOptions = {
    columnDefs: COL_DEFS,
    rowData: [],
    rowSelection: { mode: "multiRow", enableClickSelection: false },
    defaultColDef: {
      resizable: true,
      sortable: true,
      filter: true,
      editable: false,
    },
    onCellValueChanged(e) {
      const idx = State.records.findIndex(r => r.Item === e.data.Item);
      if (idx >= 0) {
        State.records[idx] = { ...State.records[idx], [e.colDef.field]: e.newValue };
      }
    },
    onSelectionChanged() {
      State.selectedRows = State.gridApi.getSelectedRows();
      const n = State.selectedRows.length;
      document.getElementById("btn-delete").disabled = n === 0;
      const selEl = document.getElementById("status-selected");
      if (n > 0) {
        selEl.style.display = "";
        document.getElementById("status-sel-n").textContent = n;
      } else {
        selEl.style.display = "none";
      }
    },
    suppressCellFocus: false,
    animateRows: true,
  };

  const el = document.getElementById("catalog-grid");
  State.gridApi = agGrid.createGrid(el, gridOptions);
}

function refreshGrid() {
  if (!State.gridApi) return;
  State.gridApi.setGridOption("rowData", State.records);
  updateUIState();
}

function updateUIState() {
  const hasData = State.records.length > 0;
  document.getElementById("btn-export-excel").disabled = !hasData;
  document.getElementById("btn-export-csv").disabled = !hasData;
  document.getElementById("btn-clear").disabled = !hasData;

  const n = State.records.length;
  document.getElementById("header-count").textContent = `${n} título${n !== 1 ? "s" : ""}`;
  document.getElementById("status-records").textContent = `📋 ${n} registro${n !== 1 ? "s" : ""}`;

  if (document.getElementById("tab-resumo").classList.contains("active")) {
    renderResumo();
  }
}

// ── Tabs ──────────────────────────────────────────────────────────────────────

function initTabs() {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      const id = "tab-" + btn.dataset.tab;
      document.getElementById(id).classList.add("active");
      if (btn.dataset.tab === "resumo") renderResumo();
    });
  });
}

// ── PDF client-side extraction ────────────────────────────────────────────────

async function extractPdfText(arrayBuffer) {
  const loadingTask = pdfjsLib.getDocument({ data: new Uint8Array(arrayBuffer) });
  const pdf = await loadingTask.promise;
  const pageTexts = [];
  for (let i = 1; i <= pdf.numPages; i++) {
    const page = await pdf.getPage(i);
    const content = await page.getTextContent();
    const lineMap = {};
    for (const item of content.items) {
      if (!item.str) continue;
      const y = Math.round(item.transform[5]);
      if (!lineMap[y]) lineMap[y] = [];
      lineMap[y].push({ x: item.transform[4], str: item.str });
    }
    const lines = Object.keys(lineMap)
      .sort((a, b) => Number(b) - Number(a))
      .map(y => lineMap[y].sort((a, b) => a.x - b.x).map(i => i.str).join(" "));
    pageTexts.push(lines.join("\n"));
  }
  return { text: pageTexts.join("\n"), pageCount: pdf.numPages };
}

async function extractZipTexts(file) {
  const zip = await JSZip.loadAsync(file);
  const results = [];
  const pdfEntries = [];
  zip.forEach((path, entry) => {
    if (!entry.dir && path.toLowerCase().endsWith(".pdf") && !path.startsWith("__MACOSX")) {
      pdfEntries.push({ path, entry });
    }
  });
  for (const { path, entry } of pdfEntries) {
    try {
      const ab = await entry.async("arraybuffer");
      const { text, pageCount } = await extractPdfText(ab);
      const filename = path.split("/").pop();
      results.push({ filename, text, page_count: pageCount });
    } catch (err) {
      console.warn(`Erro ao processar ${path}:`, err);
    }
  }
  return results;
}

// ── Upload ────────────────────────────────────────────────────────────────────

document.getElementById("btn-upload").addEventListener("click", () => {
  document.getElementById("inp-upload").click();
});

document.getElementById("inp-upload").addEventListener("change", async (e) => {
  const files = Array.from(e.target.files);
  if (!files.length) return;
  e.target.value = "";

  showSpinner("Extraindo texto dos PDFs… (pode demorar)");
  setStatus("Processando…");
  try {
    const fileTexts = [];

    for (const file of files) {
      const nameLow = file.name.toLowerCase();
      if (nameLow.endsWith(".zip")) {
        showSpinner(`Descompactando ${file.name}…`);
        const zipTexts = await extractZipTexts(file);
        fileTexts.push(...zipTexts);
      } else if (nameLow.endsWith(".pdf")) {
        showSpinner(`Extraindo texto: ${file.name}`);
        const { text, pageCount } = await extractPdfText(await file.arrayBuffer());
        fileTexts.push({ filename: file.name, text, page_count: pageCount });
      }
    }

    if (!fileTexts.length) {
      throw new Error("Nenhum PDF encontrado nos arquivos selecionados.");
    }

    showSpinner(`Extraindo metadados via IA… (${fileTexts.length} PDF(s))`);

    const res = await fetch(`${API_BASE}/api/process-text`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ files: fileTexts }),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      let detail = `HTTP ${res.status}`;
      try { detail = JSON.parse(text).detail || detail; } catch { detail = text.slice(0, 300) || detail; }
      throw new Error(detail);
    }
    const data = await res.json();
    State.records = renumber([...State.records, ...data.records]);
    refreshGrid();
    toast(`✅ ${State.records.length} título(s) processado(s)`, "success");
    setStatus(`Carregado: ${files.map(f => f.name).join(", ")}`);
  } catch (err) {
    const msg = err.message || "erro desconhecido";
    toast(`Erro ao processar: ${msg}`, "error", 12000);
    setStatus(`Erro: ${msg.slice(0, 80)}`);
  } finally {
    hideSpinner();
  }
});

// ── Import ────────────────────────────────────────────────────────────────────

document.getElementById("btn-import").addEventListener("click", () => {
  document.getElementById("inp-import").click();
});

document.getElementById("inp-import").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  e.target.value = "";

  const fd = new FormData();
  fd.append("file", file);

  showSpinner("Importando planilha…");
  try {
    const res = await fetch(`${API_BASE}/api/import`, { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    const data = await res.json();
    State.records = data.records;
    refreshGrid();
    toast(`✅ ${State.records.length} registro(s) importado(s) de "${file.name}"`, "success");
    setStatus(`Importado: ${file.name}`);
  } catch (err) {
    toast(`Erro ao importar: ${err.message}`, "error", 6000);
    setStatus("Erro ao importar");
  } finally {
    hideSpinner();
  }
});

// ── Delete ────────────────────────────────────────────────────────────────────

document.getElementById("btn-delete").addEventListener("click", () => {
  const selectedItems = new Set(State.selectedRows.map(r => r.Item));
  State.records = renumber(State.records.filter(r => !selectedItems.has(r.Item)));
  State.selectedRows = [];
  refreshGrid();
  toast(`🗑 Registros removidos`, "info");
});

// ── Clear ─────────────────────────────────────────────────────────────────────

document.getElementById("btn-clear").addEventListener("click", () => {
  if (!confirm("Tem certeza que deseja limpar todo o catálogo?")) return;
  State.records = [];
  State.selectedRows = [];
  refreshGrid();
  toast("Catálogo limpo", "info");
});

// ── Search ────────────────────────────────────────────────────────────────────

document.getElementById("search-box").addEventListener("input", (e) => {
  State.gridApi?.setGridOption("quickFilterText", e.target.value);
});

// ── Export ────────────────────────────────────────────────────────────────────

async function exportFile(endpoint, filename) {
  if (!State.records.length) return;
  showSpinner("Gerando arquivo…");
  try {
    const res = await fetch(`${API_BASE}${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ records: State.records }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    toast(`✅ ${filename} baixado`, "success");
  } catch (err) {
    toast(`Erro ao exportar: ${err.message}`, "error", 6000);
  } finally {
    hideSpinner();
  }
}

document.getElementById("btn-export-excel").addEventListener("click", () => {
  exportFile("/api/export/excel", "catalogo_grafica_educar.xlsx");
});

document.getElementById("btn-export-csv").addEventListener("click", () => {
  exportFile("/api/export/csv", "catalogo_grafica_educar.csv");
});

// ── Modal: Adicionar ──────────────────────────────────────────────────────────

document.getElementById("btn-add").addEventListener("click", () => {
  document.getElementById("form-add").reset();
  document.getElementById("modal-add").classList.add("open");
});

document.querySelectorAll("[data-close]").forEach(el => {
  el.addEventListener("click", () => {
    document.getElementById(el.dataset.close).classList.remove("open");
  });
});

document.getElementById("btn-confirm-add").addEventListener("click", () => {
  const form = document.getElementById("form-add");
  if (!form.checkValidity()) { form.reportValidity(); return; }

  const rec = emptyRecord();
  new FormData(form).forEach((val, key) => { if (key in rec) rec[key] = val; });
  rec.Item = State.records.length + 1;

  State.records = [...State.records, rec];
  refreshGrid();
  document.getElementById("modal-add").classList.remove("open");
  toast(`✅ "${rec["Título"]}" adicionado`, "success");
});

// Fechar modal clicando fora
document.getElementById("modal-add").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) e.currentTarget.classList.remove("open");
});

// ── Resumo ────────────────────────────────────────────────────────────────────

function groupCount(records, field) {
  const map = {};
  for (const r of records) {
    const key = String(r[field] || "").trim();
    if (!key) continue;
    map[key] = (map[key] || 0) + 1;
  }
  return Object.entries(map).sort((a, b) => a[0].localeCompare(b[0], "pt-BR"));
}

function groupCountByTipo(records) {
  const map = {};
  for (const r of records) {
    const title = String(r["Título"] || "");
    const parts = title.split(" - ");
    const tipo = parts[parts.length - 1].trim();
    if (!tipo) continue;
    map[tipo] = (map[tipo] || 0) + 1;
  }
  return Object.entries(map).sort((a, b) => a[0].localeCompare(b[0], "pt-BR"));
}

function tableHTML(rows) {
  if (!rows.length) return "<p style='padding:12px;color:#888;font-size:13px'>Sem dados</p>";
  return "<table>" + rows.map(([k, v]) =>
    `<tr><td>${k}</td><td>${v}</td></tr>`
  ).join("") + "</table>";
}

function renderResumo() {
  const el = document.getElementById("resumo-content");
  const records = State.records;

  if (!records.length) {
    el.innerHTML = `<div class="resumo-empty">Nenhum dado carregado.<br>
      Use <strong>Upload ZIP/PDF</strong> ou <strong>Importar XLSX/CSV</strong> para começar.</div>`;
    return;
  }

  const totalTitulos = records.length;
  const pages = records.map(r => parseInt(r["Número de páginas"]) || 0);
  const totalPaginas = pages.reduce((a, b) => a + b, 0);
  const mediaPaginas = totalTitulos ? (totalPaginas / totalTitulos).toFixed(1) : "0";

  const faixaRows = groupCount(records, "Faixa etária / nível");
  const colecaoRows = groupCount(records, "Coleção");
  const tipoRows = groupCountByTipo(records);
  const anoRows = groupCount(records, "Ano de publicação");

  el.innerHTML = `
    <div class="metrics-row">
      <div class="metric-card">
        <div class="metric-value">${totalTitulos}</div>
        <div class="metric-label">Total de títulos</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">${totalPaginas.toLocaleString("pt-BR")}</div>
        <div class="metric-label">Total de páginas</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">${mediaPaginas}</div>
        <div class="metric-label">Média páginas / título</div>
      </div>
    </div>

    <div class="resumo-tables">
      <div class="resumo-table-card">
        <h3>Por Faixa Etária / Nível</h3>
        ${tableHTML(faixaRows)}
      </div>
      <div class="resumo-table-card">
        <h3>Por Tipo</h3>
        ${tableHTML(tipoRows)}
      </div>
      <div class="resumo-table-card">
        <h3>Por Coleção</h3>
        ${tableHTML(colecaoRows)}
      </div>
      <div class="resumo-table-card">
        <h3>Por Ano de Publicação</h3>
        ${tableHTML(anoRows)}
      </div>
    </div>
  `;
}

// ── Init ──────────────────────────────────────────────────────────────────────

initTabs();
initGrid();
updateUIState();
