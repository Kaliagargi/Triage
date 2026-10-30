import React, { useEffect, useState } from 'react';
import { Map, Source, Layer } from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';

interface Incident {
  id: string;
  lat: number;
  lon: number;
  triage_score: number;
  needs: string[];
  status: string;
}

export default function TraceDashboard() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [selectedIncident, setSelectedIncident] = useState<Incident | null>(null);

  useEffect(() => {
    fetch('/v1/incidents?event_id=current-flood-2026')
      .then(res => res.json())
      .then(data => setIncidents(data))
      .catch(err => console.error("Failed to load incidents:", err));
  }, []);

  return (
    <div className="flex h-screen w-screen bg-slate-950 text-slate-100 font-sans">
      {/* Sidebar Triage Queue */}
      <div className="w-96 border-r border-slate-800 flex flex-col">
        <header className="p-4 border-b border-slate-800 bg-slate-900">
          <h1 className="text-lg font-bold tracking-tight">TRACE-India Operations</h1>
          <p className="text-xs text-slate-400">SDMA District Control Room Feed</p>
        </header>
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {incidents.map(inc => (
            <div 
              key={inc.id}
              onClick={() => setSelectedIncident(inc)}
              className={`p-3 rounded-lg border cursor-pointer transition-colors ${
                selectedIncident?.id === inc.id ? 'border-sky-500 bg-sky-950/30' : 'border-slate-800 bg-slate-900/50 hover:bg-slate-900'
              }`}
            >
              <div className="flex justify-between items-center mb-1">
                <span className="text-xs font-semibold px-2 py-0.5 rounded bg-red-950 text-red-400 border border-red-800">
                  Score: {inc.triage_score.toFixed(2)}
                </span>
                <span className="text-xs text-slate-400">{inc.status}</span>
              </div>
              <p className="text-sm font-medium">Needs: {inc.needs.join(', ')}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Map View */}
      <div className="flex-1 relative">
        <Map
          initialViewState={{ latitude: 21.1458, longitude: 79.0882, zoom: 6 }}
          style={{ width: '100%', height: '100%' }}
          mapStyle="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
        >
          {/* Render live incident pins layer */}
        </Map>
      </div>
    </div>
  );
}