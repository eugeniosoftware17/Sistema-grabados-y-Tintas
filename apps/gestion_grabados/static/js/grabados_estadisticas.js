// grabados_estadisticas.js
// Consume datos desde la API /grabados/api/registros/

const COLORS = {
    green:      '#2d8a3e',
    greenLight: '#5ab86a',
    amber:      '#d4820a',
    red:        '#c0392b',
    teal:       '#0f8a6e',
    blue:       '#1a6a9a',
    gray:       '#8a9a8e',
    grid:       'rgba(0,0,0,0.07)',
    tick:       '#6a8a72',
};

// Umbrales configurables — ajustar según la realidad de planta
const UMBRAL_VIDA_UTIL = 50; // usos acumulados a partir de los cuales se sugiere revisar/retirar el grabado
const MERMA_ALERTA     = 5;  // % de merma a partir del cual se considera alta

let charts = {};
let currentPeriod = 'todo';
let customRange = { desde: null, hasta: null };
let rawData = [];  // [{of, cliente, proceso, maquina, estado, responsables, fecha_prog, fecha_reg, ...}, ...]

// ── 1. OBTENCIÓN DE DATOS ──────────────────────────────────────
async function fetchRawData() {
    try {
        const response = await fetch('/grabados/api/registros/');
        const data = await response.json();
        
        // Mapear los nombres de campos de la API a los esperados por el dashboard
        return data.map(r => ({
            of:                r.of,
            referencia:        r.ref || '',
            descripcion:       r.desc || '',
            cliente:           r.cliente || '',
            tipo_grabado:      r.tipo || '',
            proceso:           r.proceso || '',
            maquina:           r.maquina || '',
            estado:            r.estado || '',
            ubicacion:         r.ubicacion || '',
            sobre:             r.sobre || '',
            responsables:      r.responsables || '',
            usos_acumulados:   parseInt(r.usos_acumulados) || 0,
            cantidad_formatos: parseFloat(r.cantidad) || 0,
            horas_proceso:     parseFloat(r.horas) || 0,
            peso_inicial:      parseFloat(r.peso_i) || 0,
            peso_final:        parseFloat(r.peso_f) || 0,
            perdida:           parseFloat(r.perdida) || 0,
            temp:              parseFloat(r.temp) || 0,
            rpm:               parseFloat(r.rpm) || 0,
            tiempo:            parseFloat(r.tiempo_t) || 0,
            compensacion:      r.comp || '',
            foto_dano:         r.foto_dano || '',
            fecha_prog:        r.fecha || '',
            fecha_reg:         r.fecha_reg || '',
        }));
    } catch (err) {
        console.error("Error al obtener datos de la API:", err);
        return [];
    }
}

// ── 2. FILTRO POR PERÍODO ─────────────────────────────────────
function parseDate(str) {
    if (!str || str === '—' || str.trim() === '') return null;
    const parts = str.split('/');
    if (parts.length === 3) return new Date(parts[2], parts[1]-1, parts[0]);
    return null;
}

function filterByPeriod(data, period) {
    if (period === 'personalizado') {
        const d = customRange.desde ? new Date(customRange.desde + 'T00:00:00') : null;
        const h = customRange.hasta ? new Date(customRange.hasta + 'T23:59:59') : null;
        return data.filter(r => {
            const date = parseDate(r.fecha_reg) || parseDate(r.fecha_prog);
            if (!date) return false;
            if (d && date < d) return false;
            if (h && date > h) return false;
            return true;
        });
    }

    if (period === 'todo') return data;
    const now = new Date();
    const cutoff = new Date();
    if (period === 'semana') cutoff.setDate(now.getDate() - 7);
    else if (period === 'mes')  cutoff.setMonth(now.getMonth() - 1);
    else if (period === 'año')  cutoff.setFullYear(now.getFullYear() - 1);

    return data.filter(r => {
        const d = parseDate(r.fecha_reg) || parseDate(r.fecha_prog);
        return d && d >= cutoff;
    });
}

// ── 3. HELPERS DE AGREGACIÓN ──────────────────────────────────
function splitResponsables(str) {
    if (!str || str === '—') return [];
    return str.split(/[,/]| y /i)
        .map(s => s.trim())
        .filter(Boolean)
        .map(s => s.charAt(0).toUpperCase() + s.slice(1));
}

function addToAvgMap(map, key, value) {
    if (!key) return;
    if (!map[key]) map[key] = { sum: 0, count: 0 };
    map[key].sum += value;
    map[key].count++;
}

function avgFromMap(map) {
    const out = {};
    Object.entries(map).forEach(([k, v]) => { out[k] = v.count ? v.sum / v.count : 0; });
    return out;
}

function incrementMap(map, key) {
    if (!key) return;
    map[key] = (map[key] || 0) + 1;
}

// ── 4. PROCESAMIENTO ──────────────────────────────────────────
function processData(data) {
    const counts = { proceso:{}, estado:{}, maquina:{}, cliente:{}, responsable:{} };

    const merma = {
        porMaquina: {}, porProceso: {}, porResponsable: {}, porFecha: {},
        totalPerdida: 0, totalPesoInicial: 0, registros: 0,
    };
    const tiempo = {
        porProceso: {}, porMaquina: {},
        totalEst: 0, totalReal: 0, registros: 0,
    };
    const incidencias = {
        porMaquina: {}, porResponsable: {}, porProceso: {}, total: 0,
    };
    const vidaUtil = []; // [{ of, maquina, usos }]

    data.forEach(r => {
        // Proceso
        const proc = r.proceso || 'Sin proceso';
        counts.proceso[proc] = (counts.proceso[proc] || 0) + 1;

        // Estado
        const est = r.estado || 'DESCONOCIDO';
        counts.estado[est] = (counts.estado[est] || 0) + 1;

        // Máquina
        const maq = (r.maquina && r.maquina !== '—' && r.maquina.trim()) ? r.maquina.trim() : 'Sin máquina';
        counts.maquina[maq] = (counts.maquina[maq] || 0) + 1;

        // Cliente
        const cli = r.cliente || 'Desconocido';
        counts.cliente[cli] = (counts.cliente[cli] || 0) + 1;

        // Responsables (el modelo guarda el campo en plural; puede traer varios nombres)
        const nombres = splitResponsables(r.responsables || r.responsable);
        nombres.forEach(n => { counts.responsable[n] = (counts.responsable[n] || 0) + 1; });

        // ── Merma de material (peso_inicial, peso_final y perdida, todo en gramos) ──
        const pesoIni = parseFloat(r.peso_inicial); // g
        const pesoFin = parseFloat(r.peso_final);   // g
        let perd = parseFloat(r.perdida);           // g
        if (isNaN(perd) && !isNaN(pesoIni) && !isNaN(pesoFin)) perd = pesoIni - pesoFin;
        if (!isNaN(perd) && !isNaN(pesoIni) && pesoIni > 0) {
            const pct = (perd / pesoIni) * 100;
            addToAvgMap(merma.porMaquina, maq, pct);
            addToAvgMap(merma.porProceso, proc, pct);
            nombres.forEach(n => addToAvgMap(merma.porResponsable, n, pct));
            const fecha = r.fecha_reg || r.fecha_registro || r.fecha_prog;
            if (fecha) addToAvgMap(merma.porFecha, fecha, pct);
            merma.totalPerdida += perd;
            merma.totalPesoInicial += pesoIni;
            merma.registros++;
        }

        // ── Eficiencia de tiempo, en minutos (horas_proceso×60 = estimado, tiempo = real) ──
        const horasProcesoEst = parseFloat(r.horas_proceso);
        const estMin = isNaN(horasProcesoEst) ? NaN : horasProcesoEst * 60;
        const tiempoReal = parseFloat(r.tiempo);
        if (!isNaN(estMin) && !isNaN(tiempoReal) && estMin > 0) {
            if (!tiempo.porProceso[proc]) tiempo.porProceso[proc] = { est:0, real:0, count:0 };
            tiempo.porProceso[proc].est  += estMin;
            tiempo.porProceso[proc].real += tiempoReal;
            tiempo.porProceso[proc].count++;

            if (!tiempo.porMaquina[maq]) tiempo.porMaquina[maq] = { est:0, real:0, count:0 };
            tiempo.porMaquina[maq].est  += estMin;
            tiempo.porMaquina[maq].real += tiempoReal;
            tiempo.porMaquina[maq].count++;

            tiempo.totalEst  += estMin;
            tiempo.totalReal += tiempoReal;
            tiempo.registros++;
        }

        // ── Incidencias / daños (foto_dano) ──
        if (r.foto_dano && r.foto_dano !== '—' && r.foto_dano !== '') {
            incidencias.total++;
            incrementMap(incidencias.porMaquina, maq);
            incrementMap(incidencias.porProceso, proc);
            nombres.forEach(n => incrementMap(incidencias.porResponsable, n));
        }

        // ── Vida útil del grabado (usos_acumulados) ──
        const usos = parseInt(r.usos_acumulados, 10);
        if (!isNaN(usos)) {
            vidaUtil.push({ of: r.of, maquina: maq, usos });
        }
    });

    const total      = data.length;
    const completados = counts.estado['COMPLETADO'] || 0;
    // El modelo usa EN_PROCESO; se contempla también EN_MAQUINA por compatibilidad con datos previos
    const enProceso  = (counts.estado['EN_PROCESO'] || 0) + (counts.estado['EN_MAQUINA'] || 0);
    const repetir    = counts.estado['REPETIR']    || 0;
    const pendientes = counts.estado['PENDIENTE']  || 0;
    const operarios  = Object.keys(counts.responsable).length;

    const mermaPromedio    = merma.totalPesoInicial ? (merma.totalPerdida / merma.totalPesoInicial * 100) : null;
    const desviacionTiempo = tiempo.totalEst ? ((tiempo.totalReal - tiempo.totalEst) / tiempo.totalEst * 100) : null;
    const incidenciasPct   = total ? (incidencias.total / total * 100) : 0;
    const alertasVidaUtil  = vidaUtil.filter(v => v.usos >= UMBRAL_VIDA_UTIL).length;

    return {
        total, completados, enProceso, repetir, pendientes, operarios, counts,
        merma, tiempo, incidencias, vidaUtil,
        mermaPromedio, desviacionTiempo, incidenciasPct, alertasVidaUtil,
    };
}

// ── 5. ACTUALIZAR KPIs ────────────────────────────────────────
function updateKPIs(proc) {
    const pctOk  = proc.total ? Math.round(proc.completados / proc.total * 100) : 0;
    const pctRep = proc.total ? Math.round(proc.repetir    / proc.total * 100) : 0;

    setText('kpi-total',           proc.total);
    setText('kpi-total-sub',       `${proc.enProceso} en proceso · ${proc.pendientes} pendientes`);
    setText('kpi-completados',     proc.completados);
    setText('kpi-completados-sub', `↑ ${pctOk}% tasa de éxito`);
    setText('kpi-repetir',         proc.repetir);
    setText('kpi-repetir-sub',     `${pctRep}% requiere revisión`);
    setText('kpi-operarios',       proc.operarios);
    const nombres = Object.keys(proc.counts.responsable).slice(0,3).join(' · ');
    setText('kpi-operarios-sub',   nombres || '—');

    // ── KPIs avanzados: merma, tiempo, incidencias, vida útil ──
    setText('kpi-merma', proc.mermaPromedio === null ? '—' : `${proc.mermaPromedio.toFixed(1)}%`);
    setText('kpi-merma-sub', proc.merma.registros
        ? `${proc.merma.totalPerdida.toFixed(0)} g perdidos en ${proc.merma.registros} OF con datos de peso`
        : 'sin datos de peso registrados');
    toggleClass('kpi-card-merma', 'warn', proc.mermaPromedio !== null && proc.mermaPromedio > MERMA_ALERTA);

    const signo = proc.desviacionTiempo === null ? '' : (proc.desviacionTiempo > 0 ? '+' : '');
    setText('kpi-tiempo', proc.desviacionTiempo === null ? '—' : `${signo}${proc.desviacionTiempo.toFixed(0)}%`);
    setText('kpi-tiempo-sub', proc.tiempo.registros
        ? (proc.desviacionTiempo > 0 ? 'tiempo real por encima del estimado' : 'tiempo real dentro del estimado')
        : 'sin datos de tiempo registrados');
    toggleClass('kpi-card-tiempo', 'warn', proc.desviacionTiempo !== null && proc.desviacionTiempo > 10);

    setText('kpi-incidencias', proc.incidencias.total);
    setText('kpi-incidencias-sub', `${proc.incidenciasPct.toFixed(0)}% de las OF con evidencia de daño`);
    toggleClass('kpi-card-incidencias', 'warn', proc.incidencias.total > 0);

    setText('kpi-vidautil', proc.alertasVidaUtil);
    setText('kpi-vidautil-sub', `con ${UMBRAL_VIDA_UTIL}+ usos acumulados`);
    toggleClass('kpi-card-vidautil', 'amber', proc.alertasVidaUtil > 0);
}

function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val ?? '—';
}

function toggleClass(id, cls, condition) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle(cls, !!condition);
}

// ── 6. GRÁFICAS ───────────────────────────────────────────────
function destroyChart(key) {
    if (charts[key]) { charts[key].destroy(); delete charts[key]; }
}

function hideCardIfEmpty(canvasEl, isEmpty) {
    const card = canvasEl?.closest('.stats-card');
    if (!card) return;
    card.style.display = isEmpty ? 'none' : '';
}

function buildLegend(containerId, items) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = items.map(i =>
        `<span class="legend-item">
            <span class="legend-dot" style="background:${i.color};border-radius:${i.pill?'50%':'2px'}"></span>
            ${i.label}
        </span>`
    ).join('');
}

const AXIS = {
    grid:  { color: COLORS.grid },
    ticks: { color: COLORS.tick, font: { size: 12 } },
};

// Distribución por proceso (pie)
function buildProceso(counts) {
    destroyChart('proceso');
    const labels = Object.keys(counts.proceso);
    const values = Object.values(counts.proceso);
    const total  = values.reduce((a,b)=>a+b,0);
    const palette= [COLORS.green, COLORS.amber, COLORS.teal, COLORS.blue, COLORS.gray];

    buildLegend('legend-proceso', labels.map((l,i)=>({
        label:`${l} — ${values[i]} (${Math.round(values[i]/total*100)}%)`,
        color: palette[i] || COLORS.gray,
    })));

    charts.proceso = new Chart(document.getElementById('chart-proceso'), {
        type:'pie',
        data:{ labels, datasets:[{ data:values, backgroundColor:palette, borderWidth:3, borderColor:'#fff' }] },
        options:{ responsive:true, maintainAspectRatio:false,
            plugins:{ legend:{display:false}, tooltip:{callbacks:{label:c=>` ${c.label}: ${c.parsed}`}} } },
    });
}

// Control de calidad (donut con todos los estados)
function buildCalidad(counts) {
    destroyChart('calidad');
    const stateColors = {
        'COMPLETADO': COLORS.green,
        'EN_PROCESO': COLORS.amber,
        'EN_MAQUINA': COLORS.amber,
        'REPETIR':    COLORS.red,
        'PENDIENTE':  COLORS.gray,
    };
    const labels = Object.keys(counts.estado);
    const values = Object.values(counts.estado);
    const total  = values.reduce((a,b)=>a+b,0);
    const bgs    = labels.map(l => stateColors[l] || COLORS.blue);

    buildLegend('legend-calidad', labels.map((l,i)=>({
        label:`${l.replace('_',' ')} — ${values[i]} (${Math.round(values[i]/total*100)}%)`,
        color: bgs[i], pill: l==='REPETIR',
    })));

    charts.calidad = new Chart(document.getElementById('chart-calidad'), {
        type:'doughnut',
        data:{ labels, datasets:[{ data:values, backgroundColor:bgs, borderWidth:3, borderColor:'#fff' }] },
        options:{ responsive:true, maintainAspectRatio:false, cutout:'60%',
            plugins:{ legend:{display:false}, tooltip:{callbacks:{label:c=>` ${c.label}: ${c.parsed}`}} } },
    });
}

// Uso de maquinaria (barra vertical)
function buildMaquina(counts) {
    destroyChart('maquina');
    const sorted = Object.entries(counts.maquina).sort((a,b)=>b[1]-a[1]);
    const labels = sorted.map(e=>e[0]);
    const values = sorted.map(e=>e[1]);

    charts.maquina = new Chart(document.getElementById('chart-maquina'), {
        type:'bar',
        data:{ labels, datasets:[{ label:'Grabados', data:values,
            backgroundColor: labels.map(l => l==='Sin máquina' ? COLORS.gray : COLORS.green),
            borderRadius:6, borderSkipped:false }] },
        options:{ responsive:true, maintainAspectRatio:false,
            plugins:{ legend:{display:false} },
            scales:{
                x:{ grid:{display:false}, ticks:{color:COLORS.tick, font:{size:12}} },
                y:{ ...AXIS, beginAtZero:true, ticks:{...AXIS.ticks, stepSize:1} },
            }
        },
    });
}

// Operarios (barra horizontal)
function buildOperario(counts) {
    destroyChart('operario');
    const sorted = Object.entries(counts.responsable)
        .sort((a,b)=>b[1]-a[1]).slice(0,10);
    const labels = sorted.map(e=>e[0]);
    const values = sorted.map(e=>e[1]);
    const max    = values[0] || 1;

    charts.operario = new Chart(document.getElementById('chart-operario'), {
        type:'bar',
        data:{ labels, datasets:[{ label:'Grabados', data:values,
            backgroundColor: values.map((_,i)=> i===0 ? COLORS.green : COLORS.greenLight),
            borderRadius:4, borderSkipped:false }] },
        options:{ responsive:true, maintainAspectRatio:false, indexAxis:'y',
            plugins:{ legend:{display:false} },
            scales:{
                x:{ ...AXIS, beginAtZero:true, max: Math.ceil(max*1.2) },
                y:{ grid:{display:false}, ticks:{color:COLORS.tick, font:{size:12}} },
            }
        },
    });
}

// Clientes (barra horizontal)
function buildCliente(counts) {
    destroyChart('cliente');
    const el = document.getElementById('chart-cliente');
    if (!el) return;
    const sorted = Object.entries(counts.cliente).sort((a,b)=>b[1]-a[1]).slice(0,8);
    const labels = sorted.map(e=>e[0]);
    const values = sorted.map(e=>e[1]);

    charts.cliente = new Chart(el, {
        type:'bar',
        data:{ labels, datasets:[{ label:'Órdenes', data:values,
            backgroundColor: COLORS.teal, borderRadius:4, borderSkipped:false }] },
        options:{ responsive:true, maintainAspectRatio:false, indexAxis:'y',
            plugins:{ legend:{display:false} },
            scales:{
                x:{ ...AXIS, beginAtZero:true },
                y:{ grid:{display:false}, ticks:{color:COLORS.tick, font:{size:11},
                    callback: v => v.length>18 ? v.slice(0,18)+'…' : v } },
            }
        },
    });
}

// Tendencia: estado por día (si hay fechas)
function buildTendencia(data, period) {
    destroyChart('tendencia');
    const el = document.getElementById('chart-tendencia');
    if (!el) return;

    // Agrupar por fecha de registro
    const byDate = {};
    data.forEach(r => {
        const d = r.fecha_reg || r.fecha_prog || '';
        if (!d || d==='—') return;
        if (!byDate[d]) byDate[d] = { completados:0, repetir:0, enProceso:0 };
        if (r.estado==='COMPLETADO') byDate[d].completados++;
        else if (r.estado==='REPETIR') byDate[d].repetir++;
        else if (r.estado==='EN_PROCESO' || r.estado==='EN_MAQUINA') byDate[d].enProceso++;
    });

    const labels = Object.keys(byDate).sort();
    const comp   = labels.map(d=>byDate[d].completados);
    const rep    = labels.map(d=>byDate[d].repetir);
    const maq    = labels.map(d=>byDate[d].enProceso);

    if (labels.length < 2) {
        hideCardIfEmpty(el, true);
        return;
    }
    hideCardIfEmpty(el, false);

    charts.tendencia = new Chart(el, {
        type:'line',
        data:{ labels, datasets:[
            { label:'Completados', data:comp, borderColor:COLORS.green,  backgroundColor:'rgba(45,138,62,0.08)',  fill:true, tension:.3, pointRadius:4, borderWidth:2 },
            { label:'En proceso',  data:maq,  borderColor:COLORS.amber,  backgroundColor:'rgba(212,130,10,0.06)', fill:true, tension:.3, pointRadius:4, borderWidth:2, borderDash:[5,4] },
            { label:'Repetir',     data:rep,  borderColor:COLORS.red,    backgroundColor:'rgba(192,57,43,0.06)',  fill:true, tension:.3, pointRadius:4, borderWidth:2, borderDash:[3,3] },
        ]},
        options:{ responsive:true, maintainAspectRatio:false,
            plugins:{ legend:{display:false},
                tooltip:{ mode:'index', intersect:false } },
            scales:{
                x:{ grid:{color:COLORS.grid}, ticks:{color:COLORS.tick, font:{size:12}} },
                y:{ ...AXIS, beginAtZero:true, ticks:{...AXIS.ticks, stepSize:1} },
            }
        },
    });

    // Leyenda manual de tendencia
    buildLegend('legend-tendencia', [
        {label:'Completados', color:COLORS.green},
        {label:'En proceso',  color:COLORS.amber},
        {label:'Repetir',     color:COLORS.red},
    ]);
}

// ── Merma por máquina (% promedio de pérdida de material) ──
function buildMerma(merma) {
    destroyChart('merma');
    const el = document.getElementById('chart-merma');
    if (!el) return;

    const avgPorMaquina = avgFromMap(merma.porMaquina);
    const sorted = Object.entries(avgPorMaquina).sort((a,b)=>b[1]-a[1]);
    const labels = sorted.map(e=>e[0]);
    const values = sorted.map(e=>Number(e[1].toFixed(2)));

    hideCardIfEmpty(el, labels.length === 0);
    if (!labels.length) return;

    charts.merma = new Chart(el, {
        type: 'bar',
        data: { labels, datasets: [{
            label: '% merma promedio',
            data: values,
            backgroundColor: values.map(v => v >= MERMA_ALERTA ? COLORS.red : COLORS.amber),
            borderRadius: 6, borderSkipped: false,
        }]},
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend:{display:false}, tooltip:{callbacks:{label:c=>` ${c.parsed.y}% merma promedio`}} },
            scales: {
                x: { grid:{display:false}, ticks:{color:COLORS.tick, font:{size:12}} },
                y: { ...AXIS, beginAtZero:true, ticks:{...AXIS.ticks, callback:v=>v+'%'} },
            }
        }
    });
}

// ── Tiempo estimado (horas_proceso) vs. real (tiempo), por proceso ──
function buildTiempo(tiempo) {
    destroyChart('tiempo');
    const el = document.getElementById('chart-tiempo');
    if (!el) return;

    const entries = Object.entries(tiempo.porProceso);
    hideCardIfEmpty(el, entries.length === 0);
    if (!entries.length) return;

    const labels = entries.map(e=>e[0]);
    const est  = entries.map(([,v]) => Number((v.est  / v.count).toFixed(0)));
    const real = entries.map(([,v]) => Number((v.real / v.count).toFixed(0)));

    buildLegend('legend-tiempo', [
        { label: 'Estimado (min)', color: COLORS.gray },
        { label: 'Real (min)',     color: COLORS.blue },
    ]);

    charts.tiempo = new Chart(el, {
        type: 'bar',
        data: { labels, datasets: [
            { label:'Estimado', data:est,  backgroundColor: COLORS.gray, borderRadius:6, borderSkipped:false },
            { label:'Real',     data:real, backgroundColor: COLORS.blue, borderRadius:6, borderSkipped:false },
        ]},
        options: {
            responsive:true, maintainAspectRatio:false,
            plugins:{ legend:{display:false} },
            scales: {
                x:{ grid:{display:false}, ticks:{color:COLORS.tick, font:{size:12}} },
                y:{ ...AXIS, beginAtZero:true },
            }
        }
    });
}

// ── Vida útil del grabado (usos_acumulados) ──
function buildVidaUtil(vidaUtil) {
    destroyChart('vidautil');
    const el = document.getElementById('chart-vidautil');
    if (!el) return;

    hideCardIfEmpty(el, vidaUtil.length === 0);
    if (!vidaUtil.length) return;

    const sorted = [...vidaUtil].sort((a,b)=>b.usos-a.usos).slice(0,10);
    const labels = sorted.map(v => `OF ${v.of}`);
    const values = sorted.map(v => v.usos);

    buildLegend('legend-vidautil', [
        { label:`Por debajo del umbral (${UMBRAL_VIDA_UTIL})`, color: COLORS.green },
        { label:`En alerta (≥ ${UMBRAL_VIDA_UTIL} usos)`,       color: COLORS.red },
    ]);

    charts.vidautil = new Chart(el, {
        type: 'bar',
        data: { labels, datasets: [{
            label: 'Usos acumulados',
            data: values,
            backgroundColor: values.map(v => v >= UMBRAL_VIDA_UTIL ? COLORS.red : COLORS.green),
            borderRadius: 4, borderSkipped: false,
        }]},
        options: {
            responsive:true, maintainAspectRatio:false, indexAxis:'y',
            plugins:{ legend:{display:false} },
            scales: {
                x: { ...AXIS, beginAtZero:true },
                y: { grid:{display:false}, ticks:{color:COLORS.tick, font:{size:12}} },
            }
        }
    });
}

// ── Incidencias / daños por máquina (foto_dano) ──
function buildIncidencias(incidencias) {
    destroyChart('incidencias');
    const el = document.getElementById('chart-incidencias');
    if (!el) return;

    const entries = Object.entries(incidencias.porMaquina).sort((a,b)=>b[1]-a[1]);
    hideCardIfEmpty(el, entries.length === 0);
    if (!entries.length) return;

    const labels = entries.map(e=>e[0]);
    const values = entries.map(e=>e[1]);

    charts.incidencias = new Chart(el, {
        type: 'bar',
        data: { labels, datasets: [{
            label: 'Incidencias',
            data: values,
            backgroundColor: COLORS.red,
            borderRadius: 6, borderSkipped: false,
        }]},
        options: {
            responsive:true, maintainAspectRatio:false,
            plugins:{ legend:{display:false} },
            scales: {
                x:{ grid:{display:false}, ticks:{color:COLORS.tick, font:{size:12}} },
                y:{ ...AXIS, beginAtZero:true, ticks:{...AXIS.ticks, stepSize:1} },
            }
        }
    });
}

// ── 7. MAIN ───────────────────────────────────────────────────
async function cargarEstadisticas(period) {
    period = period || currentPeriod;
    currentPeriod = period;

    document.querySelectorAll('.period-btn').forEach(btn =>
        btn.classList.toggle('active', btn.dataset.period === period)
    );

    // Si rawData está vacío, lo cargamos. Si es por "Actualizar", forzamos recarga.
    if (rawData.length === 0) {
        rawData = await fetchRawData();
    }

    const filtered = filterByPeriod(rawData, period);
    const proc     = processData(filtered);

    updateKPIs(proc);
    buildTendencia(filtered, period);
    buildProceso(proc.counts);
    buildCalidad(proc.counts);
    buildMaquina(proc.counts);
    buildOperario(proc.counts);
    buildCliente(proc.counts);
    buildMerma(proc.merma);
    buildTiempo(proc.tiempo);
    buildVidaUtil(proc.vidaUtil);
    buildIncidencias(proc.incidencias);

    // Mostrar total filtrado en label
    const lbl = document.getElementById('trend-period-label');
    if (lbl) lbl.textContent = `${filtered.length} registros`;
}

// Función para forzar actualización (limpia caché local)
async function refreshStats() {
    rawData = [];
    await cargarEstadisticas(currentPeriod);
}

// Función para rango personalizado (llamada desde el HTML)
window.aplicarRangoPersonalizado = function() {
    const d = document.getElementById('stats-fecha-desde').value;
    const h = document.getElementById('stats-fecha-hasta').value;
    
    if (!d && !h) {
        alert("Por favor selecciona al menos una fecha.");
        return;
    }

    customRange.desde = d || null;
    customRange.hasta = h || null;
    
    // Desactivar botones de período predefinido
    document.querySelectorAll('.period-btn').forEach(btn => btn.classList.remove('active'));
    
    cargarEstadisticas('personalizado');
};

// ── 8. INIT ───────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.period-btn').forEach(btn =>
        btn.addEventListener('click', () => cargarEstadisticas(btn.dataset.period))
    );
    
    // Re-vincular botón de actualizar
    const refreshBtn = document.querySelector('.refresh-btn');
    if (refreshBtn) {
        refreshBtn.onclick = refreshStats;
    }

    cargarEstadisticas('todo');
});
