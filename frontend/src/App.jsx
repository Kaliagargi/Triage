import React, { useCallback, useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import './index.css';

const MAP_STYLE = 'https://demotiles.maplibre.org/style.json';
const API_BASE = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');
const EMPTY_FEATURES = { type: 'FeatureCollection', features: [] };

async function fetchJson(path, options) {
  const response = await fetch(\`\${API_BASE}\${path}\`, options);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || \`Request failed: \${response.status}\`);
  }
  return response.json();
}

export default function App() {
  const mapContainer = useRef(null);
  const [map, setMap] = useState(null);
  const [styleReady, setStyleReady] = useState(false);
  const [incidentData, setIncidentData] = useState(EMPTY_FEATURES);
  const [silentData, setSilentData] = useState(EMPTY_FEATURES);
  const [destinations, setDestinations] = useState(null);
  const [showSilent, setShowSilent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [silentMeta, setSilentMeta] = useState(null);

  const refreshIncidentData = useCallback(async () => {
    const data = await fetchJson('/api/map-data');
    setIncidentData(data);
  }, []);

  const refreshSilentData = useCallback(async () => {
    const data = await fetchJson('/api/silent-zones/latest');
    setSilentData(data);
    setSilentMeta(data.metadata || null);
  }, []);

  const refreshAll = useCallback(async () => {
    setError('');
    try {
      await Promise.all([refreshIncidentData(), refreshSilentData()]);
    } catch (err) {
      setError(err.message || 'Could not load map data');
    }
  }, [refreshIncidentData, refreshSilentData]);

  const runSilentZoneAnalysis = useCallback(async () => {
    setBusy(true);
    setError('');
    try {
      const result = await fetchJson('/api/silent-zones/run', { method: 'POST' });
      await refreshAll();
      setShowSilent(true);
      if (!result.cell_count) {
        setError('No geolocated incidents yet. Add reports with resolved locations, then calculate again.');
      }
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

  useEffect(() => {
    refreshAll();
    fetchJson('/api/destinations')
      .then(setDestinations)
      .catch(() => setDestinations(null));
  }, [refreshAll]);

  useEffect(() => {
    if (!map || !styleReady) return;

    const ensureSource = (id, data) => {
      const source = map.getSource(id);
      if (source) source.setData(data);
      else map.addSource(id, { type: 'geojson', data });
    };

    ensureSource('incidents', incidentData);
    ensureSource('silent-zones', silentData);

    if (!map.getLayer('uncertainty-fill')) {
      map.addLayer({
        id: 'uncertainty-fill',
        type: 'fill',
        source: 'incidents',
        filter: ['==', ['get', 'type'], 'uncertainty_area'],
        paint: {
          'fill-color': '#f59e0b',
          'fill-opacity': 0.16,
          'fill-outline-color': '#f59e0b',
        },
      });
    }

    if (!map.getLayer('incident-density')) {
      map.addLayer({
        id: 'incident-density',
        type: 'heatmap',
        source: 'incidents',
        filter: ['==', ['get', 'type'], 'incident'],
        maxzoom: 15,
        paint: {
          'heatmap-intensity': 1.1,
          'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 5, 18, 12, 36],
          'heatmap-opacity': 0.72,
        },
      });
    }

    if (!map.getLayer('incident-points')) {
      map.addLayer({
        id: 'incident-points',
        type: 'circle',
        source: 'incidents',
        filter: ['==', ['get', 'type'], 'incident'],
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 3, 13, 7],
          'circle-color': [
            'match', ['get', 'urgency'],
            'high', '#ef4444',
            'medium', '#f59e0b',
            '#38bdf8',
          ],
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 1,
        },
      });
    }

    if (!map.getLayer('silent-zone-fill')) {
      map.addLayer({
        id: 'silent-zone-fill',
        type: 'fill',
        source: 'silent-zones',
        layout: { visibility: showSilent ? 'visible' : 'none' },
        paint: {
          'fill-color': [
            'interpolate', ['linear'], ['get', 'silent_score'],
            0, '#fef3c7',
            0.5, '#fb923c',
            1, '#dc2626',
          ],
          'fill-opacity': 0.42,
          'fill-outline-color': '#7f1d1d',
        },
      });
    } else {
      map.setLayoutProperty('silent-zone-fill', 'visibility', showSilent ? 'visible' : 'none');
    }

    if (!map.getLayer('silent-zone-outline')) {
      map.addLayer({
        id: 'silent-zone-outline',
        type: 'line',
        source: 'silent-zones',
        layout: { visibility: showSilent ? 'visible' : 'none' },
        paint: { 'line-color': '#7f1d1d', 'line-width': 1 },
      });
    } else {
      map.setLayoutProperty('silent-zone-outline', 'visibility', showSilent ? 'visible' : 'none');
    }

    if (destinations && !map.getSource('destinations')) {
      map.addSource('destinations', { type: 'geojson', data: destinations });
      map.addLayer({
        id: 'destinations',
        type: 'circle',
        source: 'destinations',
        paint: {
          'circle-radius': 5,
          'circle-color': '#4ecca3',
          'circle-stroke-width': 1,
          'circle-stroke-color': '#ffffff',
        },
      });
    }
  }, [map, styleReady, incidentData, silentData, destinations, showSilent]);

  const incidentCount = incidentData.features.filter(
    (feature) => feature.properties?.type === 'incident',
  ).length;
  const uncertaintyCount = incidentData.features.filter(
    (feature) => feature.properties?.type === 'uncertainty_area',
  ).length;
  const silentCount = silentData.features.length;

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
            {busy ? 'Calculating…' : 'Recalculate silent zones'}
          </button>
          <button
            className="toggle-btn"
            onClick={() => setShowSilent((current) => !current)}
            aria-pressed={showSilent}
          >
            {showSilent ? 'Before: Reports only' : 'After: Add silent zones'}
          </button>
        </div>
      </header>

      <section className="map-shell">
        <div ref={mapContainer} className="map-container" />
        <aside className="map-panel">
          <h2>{showSilent ? 'After: reporting gaps added' : 'Before: incident density'}</h2>
          <p>
            {showSilent
              ? 'Incident density is shown together with cells that have fewer reports than the illustrative baseline.'
              : 'The heat layer shows geolocated incident reports. Switch to the after view to add silent-zone scores.'}
          </p>
          <div className="stat-grid">
            <div><strong>{incidentCount}</strong><span>Incident points</span></div>
            <div><strong>{uncertaintyCount}</strong><span>Approximate areas</span></div>
            <div><strong>{silentCount}</strong><span>Grid cells</span></div>
          </div>
          <div className="legend">
            <span><i className="legend-dot urgent" /> High urgency</span>
            <span><i className="legend-dot medium" /> Medium urgency</span>
            <span><i className="legend-dot low" /> Other / unknown</span>
            <span><i className="legend-area" /> Approximate location</span>
            <span><i className="legend-silent" /> Higher silence score</span>
          </div>
          {silentMeta?.quality_flag && (
            <p className="quality-note">
              {silentMeta.quality_flag}: current scores use configurable demo baselines, not measured exposure or vulnerability.
            </p>
          )}
          {error && <p className="error-note" role="alert">{error}</p>}
        </aside>
      </section>
    </main>
  );
}
