import React, { useCallback, useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import './index.css';

const MAP_STYLE = {
  version: 8,
  sources: {},
  layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#111827' } }],
};
const API_BASE = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');
const EMPTY_FEATURES = { type: 'FeatureCollection', features: [] };

async function fetchJson(path, options) {
  const response = await fetch(\`${API_BASE}${path}\`, options);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || \`Request failed: ${response.status}\`);
  }
  return response.json();
}

export default function App() {
  const mapContainer = useRef(null);
  const [map, setMap] = useState(null);
  const [styleReady, setStyleReady] = useState(false);
  const [incidentData, setIncidentData] = useState(EMPTY_FEATURES);
  const [silentData, setSilentData] = useState(EMPTY_FEATURES);
  const [showSilent, setShowSilent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [silentMeta, setSilentMeta] = useState(null);
  const [audioFile, setAudioFile] = useState(null);
  const [report, setReport] = useState(null);
  const [confirmed, setConfirmed] = useState(false);

  const refreshIncidentData = useCallback(async () => {
    setIncidentData(await fetchJson('/api/map-data'));
  }, []);

  const refreshSilentData = useCallback(async () => {
    const data = await fetchJson('/api/silent-zones/latest');
    setSilentData(data);
    setSilentMeta(data.metadata || null);
  }, []);

  const refreshAll = useCallback(async () => {
    try {
      await Promise.all([refreshIncidentData(), refreshSilentData()]);
    } catch (err) {
      setError(err.message || 'Could not load map data');
    }
  }, [refreshIncidentData, refreshSilentData]);

  const uploadReport = useCallback(async () => {
    if (!audioFile) return;
    setBusy(true);
    setError('');
    setReport(null);
    setConfirmed(false);
    try {
      const form = new FormData();
      form.append('file', audioFile);
      const result = await fetchJson('/api/reports', { method: 'POST', body: form });
      setReport(result);
      await refreshAll();
    } catch (err) {
      setError(err.message || 'Audio upload failed');
    } finally {
      setBusy(false);
    }
  }, [audioFile, refreshAll]);

  const confirmIncident = useCallback(async () => {
    if (!report?.incident?.id) return;
    setBusy(true);
    setError('');
    try {
      await fetchJson(\`/api/incidents/${report.incident.id}\`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ review_status: 'confirmed' }),
      });
      setConfirmed(true);
      await refreshAll();
    } catch (err) {
      setError(err.message || 'Could not confirm incident');
    } finally {
      setBusy(false);
    }
  }, [report, refreshAll]);

  const runSilentZoneAnalysis = useCallback(async () => {
    setBusy(true);
    setError('');
    try {
      const result = await fetchJson('/api/silent-zones/run', { method: 'POST' });
      await refreshAll();
      setShowSilent(true);
      if (!result.cell_count) setError('No geolocated incidents yet. Add reports with resolved locations, then calculate again.');
    } catch (err) {
      setError(err.message || 'Silent-zone analysis failed');
    } finally {
      setBusy(false);
    }
  }, [refreshAll]);

  useEffect(() => {
    if (!mapContainer.current) return undefined;
    const instance = new maplibregl.Map({
      container: mapContainer.current,
      style: MAP_STYLE,
      center: [77.5, 28.5],
      zoom: 8,
    });
    instance.addControl(new maplibregl.NavigationControl(), 'top-right');
    instance.on('load', () => setStyleReady(true));
    setMap(instance);
    return () => instance.remove();
  }, []);

  useEffect(() => { refreshAll(); }, [refreshAll]);

  useEffect(() => {
    if (!map || !styleReady) return;
    const ensureSource = (id, data) => {
      const source = map.getSource(id);
      if (source) source.setData(data);
      else map.addSource(id, { type: 'geojson', data });
    };
    ensureSource('incidents', incidentData);
    ensureSource('silent-zones', silentData);

    if (!map.getLayer('uncertainty-fill')) map.addLayer({
      id: 'uncertainty-fill', type: 'fill', source: 'incidents',
      filter: ['==', ['get', 'type'], 'uncertainty_area'],
      paint: { 'fill-color': '#f59e0b', 'fill-opacity': 0.16, 'fill-outline-color': '#f59e0b' },
    });
    if (!map.getLayer('incident-density')) map.addLayer({
      id: 'incident-density', type: 'heatmap', source: 'incidents',
      filter: ['==', ['get', 'type'], 'incident'], maxzoom: 15,
      paint: { 'heatmap-intensity': 1.1, 'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 5, 18, 12, 36], 'heatmap-opacity': 0.72 },
    });
    if (!map.getLayer('incident-points')) map.addLayer({
      id: 'incident-points', type: 'circle', source: 'incidents',
      filter: ['==', ['get', 'type'], 'incident'],
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 3, 13, 7],
        'circle-color': ['match', ['get', 'urgency'], 'high', '#ef4444', 'medium', '#f59e0b', '#38bdf8'],
        'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1,
      },
    });
    if (!map.getLayer('silent-zone-fill')) map.addLayer({
      id: 'silent-zone-fill', type: 'fill', source: 'silent-zones',
      layout: { visibility: showSilent ? 'visible' : 'none' },
      paint: {
        'fill-color': ['interpolate', ['linear'], ['get', 'silent_score'], 0, '#fef3c7', 0.5, '#fb923c', 1, '#dc2626'],
        'fill-opacity': 0.42, 'fill-outline-color': '#7f1d1d',
      },
    });
    else map.setLayoutProperty('silent-zone-fill', 'visibility', showSilent ? 'visible' : 'none');
    if (!map.getLayer('silent-zone-outline')) map.addLayer({
      id: 'silent-zone-outline', type: 'line', source: 'silent-zones',
      layout: { visibility: showSilent ? 'visible' : 'none' },
      paint: { 'line-color': '#7f1d1d', 'line-width': 1 },
    });
    else map.setLayoutProperty('silent-zone-outline', 'visibility', showSilent ? 'visible' : 'none');
  }, [map, styleReady, incidentData, silentData, showSilent]);

  const incidentCount = incidentData.features.filter((f) => f.properties?.type === 'incident').length;
  const uncertaintyCount = incidentData.features.filter((f) => f.properties?.type === 'uncertainty_area').length;

  return (
    <main className="app-container">
      <header className="app-header">
        <div>
          <h1>TRACE-India</h1>
          <p className="header-subtitle">Community incident reports and reporting gaps</p>
        </div>
        <div className="header-actions">
          <button className="secondary-btn" onClick={refreshAll}>Refresh map</button>
          <button className="secondary-btn" onClick={runSilentZoneAnalysis} disabled={busy}>
            {busy ? 'Working…' : 'Recalculate silent zones'}
          </button>
          <button className="toggle-btn" onClick={() => setShowSilent((v) => !v)} aria-pressed={showSilent}>
            {showSilent ? 'Before: Reports only' : 'After: Add silent zones'}
          </button>
        </div>
      </header>

      <section className="upload-panel">
        <div className="upload-controls">
          <label htmlFor="audio-report"><strong>Submit a community voice report</strong></label>
          <input id="audio-report" type="file" accept="audio/*,.wav,.mp3,.webm,.ogg,.m4a,.aac"
            onChange={(event) => setAudioFile(event.target.files?.[0] || null)} />
          <button className="toggle-btn" onClick={uploadReport} disabled={!audioFile || busy}>
            {busy ? 'Processing…' : 'Upload and extract'}
          </button>
        </div>
        {report && (
          <div className="transcript-card">
            <div className="transcript-heading">
              <strong>Transcript and extracted incident</strong>
              <span className={report.transcript_mode === 'CACHED' ? 'mode-badge cached' : 'mode-badge live'}>
                {report.transcript_mode || 'UNKNOWN'}
              </span>
            </div>
            <p className="transcript-text">{report.transcript_text || 'No transcript returned.'}</p>
            <div className="extraction-summary">
              <span>Hazard: <b>{report.extraction?.hazard_type || 'Unclassified'}</b></span>
              <span>Urgency: <b>{report.extraction?.urgency || 'Unknown'}</b></span>
              <span>Landmark: <b>{report.extraction?.anchor_name || 'Not resolved'}</b></span>
              <span>Relation: <b>{report.extraction?.spatial_relation || 'Not stated'}</b></span>
            </div>
            {report.incident?.id && (
              <button className="secondary-btn" onClick={confirmIncident} disabled={busy || confirmed}>
                {confirmed ? 'Incident confirmed' : 'Confirm extracted incident'}
              </button>
            )}
            {!report.incident?.id && <p className="quality-note">No incident record was produced; this report needs manual review.</p>}
          </div>
        )}
      </section>

      <section className="map-shell">
        <div ref={mapContainer} className="map-container" />
        <aside className="map-panel">
          <h2>{showSilent ? 'After: reporting gaps added' : 'Before: incident density'}</h2>
          <p>{showSilent
            ? 'Incident density is shown with cells that have fewer reports than the illustrative baseline.'
            : 'The heat layer shows geolocated incident reports. Switch to the after view to add silent-zone scores.'}</p>
          <div className="stat-grid">
            <div><strong>{incidentCount}</strong><span>Incident points</span></div>
            <div><strong>{uncertaintyCount}</strong><span>Approximate areas</span></div>
            <div><strong>{silentData.features.length}</strong><span>Grid cells</span></div>
          </div>
          <div className="legend">
            <span><i className="legend-dot urgent" /> High urgency</span>
            <span><i className="legend-dot medium" /> Medium urgency</span>
            <span><i className="legend-dot low" /> Other / unknown</span>
            <span><i className="legend-area" /> Approximate location</span>
            <span><i className="legend-silent class-one" /> Class 1 · moderate reporting gap</span>
            <span><i className="legend-silent class-two" /> Class 2 · high reporting gap</span>
          </div>
          {silentMeta?.quality_flag && <p className="quality-note">
            {silentMeta.quality_flag}: scores use configurable demo baselines, not measured exposure or vulnerability.
          </p>}
          {error && <p className="error-note" role="alert">{error}</p>}
        </aside>
      </section>
    </main>
  );
}
