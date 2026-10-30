import React, { useEffect, useState, useCallback } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import './index.css';

const MAPBOX_STYLE = 'https://demotiles.maplibre.org/style.json'; // Open source style

export default function App() {
  const [map, setMap] = useState(null);
  const [geoData, setGeoData] = useState(null);
  const [destinations, setDestinations] = useState(null);
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
      const resp = await fetch(`${import.meta.env.VITE_API_URL}/api/map-data`);
      const data = await resp.json();
      setGeoData(data);
    } catch (e) {
      console.error('Failed to load map data', e);
    }
  }, []);

  // Fetch destinations data
  const fetchDestinations = useCallback(async () => {
    try {
      const resp = await fetch(`${import.meta.env.VITE_API_URL}/api/destinations`);
      const data = await resp.json();
      setDestinations(data);
    } catch (e) {
      console.error('Failed to load destinations', e);
    }
  }, []);

  // Load destinations on mount
  useEffect(() => {
    fetchDestinations();
  }, [fetchDestinations]);

  // Add destinations layer when map and destinations are ready
  useEffect(() => {
    if (!map || !destinations) return;
    // Remove existing layer if present
    if (map.getSource('destinations')) {
      map.removeLayer('destinations');
      map.removeSource('destinations');
    }
    map.addSource('destinations', { type: 'geojson', data: destinations });
    map.addLayer({
      id: 'destinations',
      type: 'circle',
      source: 'destinations',
      filter: ['==', '$type', 'Point'],
      paint: {
        'circle-radius': 6,
        'circle-color': '#4ecca3', // teal for destinations
        'circle-stroke-width': 1,
        'circle-stroke-color': '#fff',
      },
    });
  }, [map, destinations]);


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
