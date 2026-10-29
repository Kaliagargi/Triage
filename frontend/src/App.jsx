import React, { useEffect, useState, useCallback } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import './index.css';

const MAPBOX_STYLE = 'https://demotiles.maplibre.org/style.json'; // Open source style

export default function App() {
  const [map, setMap] = useState(null);
  const [geoData, setGeoData] = useState(null);
  const [showSilent, setShowSilent] = useState(false);

  // Initialize map once
  useEffect(() => {
    const mapInstance = new maplibregl.Map({
      container: 'map',
      style: MAPBOX_STYLE,
      center: [77.5, 28.5], // demo center
      zoom: 9,
    });
    mapInstance.addControl(new maplibregl.NavigationControl());
    setMap(mapInstance);
    return () => mapInstance.remove();
  }, []);

  // Fetch map data once
  const fetchMapData = useCallback(async () => {
    try {
      const resp = await fetch('/api/map-data');
      const data = await resp.json();
      setGeoData(data);
    } catch (e) {
      console.error('Failed to load map data', e);
    }
  }, []);

  // Load data on mount
  useEffect(() => {
    fetchMapData();
  }, [fetchMapData]);

  // Add or update layers when map and data are ready
  useEffect(() => {
    if (!map || !geoData) return;

    // Remove existing sources/layers if they exist (idempotent)
    if (map.getSource('demo-data')) {
      map.removeLayer('reports');
      map.removeLayer('silent-zones');
      map.removeSource('demo-data');
    }
    map.addSource('demo-data', {
      type: 'geojson',
      data: geoData,
    });
    // Report points layer
    map.addLayer({
      id: 'reports',
      type: 'circle',
      source: 'demo-data',
      filter: ['==', '$type', 'Point'],
      paint: {
        'circle-radius': 6,
        'circle-color': '#ff6b6b',
        'circle-stroke-width': 1,
        'circle-stroke-color': '#fff',
      },
    });
    // Silent zone polygon layer (initially hidden)
    map.addLayer({
      id: 'silent-zones',
      type: 'fill',
      source: 'demo-data',
      filter: ['==', '$type', 'Polygon'],
      layout: { visibility: showSilent ? 'visible' : 'none' },
      paint: {
        'fill-color': '#4ecca3',
        'fill-opacity': 0.4,
      },
    });
    // Update visibility when toggle changes
    map.setLayoutProperty('silent-zones', 'visibility', showSilent ? 'visible' : 'none');
  }, [map, geoData, showSilent]);

  return (
    <div className="app-container">
      <header className="app-header">
        <h1>TRACE‑India Demo</h1>
        <button className="toggle-btn" onClick={() => setShowSilent(!showSilent)}>
          {showSilent ? 'Hide' : 'Show'} Silent Zones
        </button>
      </header>
      <div id="map" className="map-container" />
    </div>
  );
}
