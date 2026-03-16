import { useState, useEffect } from 'react';
import { Building, AlertTriangle, ThermometerSun, Droplets, Zap, Wind } from 'lucide-react';
import { supabase } from '../lib/supabase';
import { API_URL } from '../lib/api';
import { useAuth } from '../modules/auth/AuthContext';

interface Building {
  id: string;
  name: string;
  address: string;
  floors: string;
  sqft: string;
  occupancy: number;
  status: 'operational' | 'warning' | 'error';
  // Environmental Metrics
  temperature: number;
  humidity: number;
  energyUsage: number;
  utilization: number;
  // System Health
  hvacHealth?: number;
  electricalHealth?: number;
  waterHealth?: number;
  fireSafetyHealth?: number;
  city?: string;
  state?: string;
  country?: string;
}

interface EnvironmentalData {
  temperature: number;
  humidity: number;
  airQuality: string;
  energyUsage: number;
}


export function BuildingView() {
  const { profile } = useAuth();
  const [selectedBuilding, setSelectedBuilding] = useState<string>('tower-a');
  const [showAddBuilding, setShowAddBuilding] = useState(false);
  const [showDeleteBuilding, setShowDeleteBuilding] = useState(false);
  const [deleteSearchQuery, setDeleteSearchQuery] = useState('');
  const [buildings, setBuildings] = useState<Building[]>([]);
  const [environmentals, setEnvironmentals] = useState<EnvironmentalData>({
    temperature: 72.0,
    humidity: 45.0,
    airQuality: 'Good',
    energyUsage: 2450.0,
  });
  const [newBuilding, setNewBuilding] = useState({
    name: '',
    address: '',
    city: '',
    state: '',
    country: '',
    floors: '',
    sqft: '',
  });


  const loadBuildings = async () => {
    try {
      const companyParam = profile?.company ? `?companyId=${encodeURIComponent(profile.company)}` : '';
      const response = await fetch(`${API_URL}/_api/buildings${companyParam}`);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();

      if (data) {
        // Backend now returns the exact shape we need (including health fields)
        const mappedBuildings = data.map((b: Building) => ({
          ...b,
          // Ensure defaults if backend sends partials (though backend logic handles this mostly)
          floors: String(b.floors || '0'),
          sqft: String(b.sqft || '0'),
          occupancy: b.occupancy ?? 0,
          status: b.status ?? 'operational',
          hvacHealth: b.hvacHealth ?? 98,
          electricalHealth: b.electricalHealth ?? 100,
          waterHealth: b.waterHealth ?? 100,
          fireSafetyHealth: b.fireSafetyHealth ?? 100,
          city: b.city || '',
          state: b.state || '',
          country: b.country || ''
        }));
        setBuildings(mappedBuildings);

        // If selected building is not in the list (or initial load), select the first one
        if (mappedBuildings.length > 0) {
          setSelectedBuilding(prev => {
            const found = mappedBuildings.find((b: Building) => b.id === prev);
            return found ? prev : mappedBuildings[0].id;
          });
        }
      }
    } catch (err: any) {
      console.error('Error in loadBuildings:', err);
    }
  };

  // Fetch buildings and set up realtime subscription (scoped to company)
  useEffect(() => {
    if (!profile?.company) return; // Wait for profile to load

    loadBuildings();

    // Realtime subscription disabled per user request to stop auto-refresh behavior
    // const channel = supabase
    //   .channel('public:Building')
    //   .on(...)
    //   .subscribe();

    // return () => {
    //   supabase.removeChannel(channel);
    // };
  }, [profile?.company]);

  // Update environmental state whenever the selected building record updates in the main 'buildings' array
  useEffect(() => {
    const current = buildings.find(b => b.id === selectedBuilding);
    console.log('Syncing environmentals. Selected:', selectedBuilding, 'Available IDs:', buildings.map(b => b.id));
    if (current) {
      setEnvironmentals({
        temperature: current.temperature || 72,
        humidity: current.humidity || 45,
        airQuality: (current as any).airQuality || (current.utilization > 80 ? 'Good' : 'Excellent'),
        energyUsage: current.energyUsage || 2450
      });
    }
  }, [buildings, selectedBuilding]);


  // Prepare data for display
  const buildingsWithDefaults = buildings.map(building => ({
    ...building,
    alerts: 0 // Placeholder logic for alerts count
  }));

  const selected = buildingsWithDefaults.find(b => b.id === selectedBuilding) || buildingsWithDefaults[0];

  // Building systems - default to Operational for now
  const systems = selected ? [
    {
      name: 'HVAC',
      status: 'good',
      value: 'N/A',
      icon: Wind,
      alert: undefined,
    },
    {
      name: 'Electrical',
      status: 'good',
      value: 'N/A',
      icon: Zap,
      alert: undefined
    },
    {
      name: 'Water',
      status: 'good',
      value: 'N/A',
      icon: Droplets,
      alert: undefined
    },
    {
      name: 'Fire Safety',
      status: 'good',
      value: 'N/A',
      icon: AlertTriangle,
      alert: undefined
    },
  ] : [];

  const details = {
    systems: systems,
    environmentals: environmentals,
    // Keep hardcoded legacy sections for now if they are used, or empty if commented out in JSX
    recentIssues: [],
    upcomingMaintenance: []
  };

  const handleInputChange = (field: keyof typeof newBuilding, value: string) => {
    setNewBuilding(prev => ({ ...prev, [field]: value }));
  };

  const handleCloseDropdown = () => {
    setShowAddBuilding(false);
    setNewBuilding({ name: '', address: '', city: '', state: '', country: '', floors: '', sqft: '' });
  };

  const handleAddBuilding = async () => {
    if (!newBuilding.name || !newBuilding.address || !newBuilding.floors || !newBuilding.sqft) {
      alert("Please fill in all required fields (Name, Address, Floors, SqFt)");
      return;
    }

    const tempId = `temp-${Date.now()}`;
    const payload = { ...newBuilding, companyId: profile?.company || 'default' };

    // Optimistic Update
    const optimisticBuilding: any = {
      id: tempId,
      ...newBuilding,
      companyId: payload.companyId,
      floors: String(newBuilding.floors || '0'),
      sqft: String(newBuilding.sqft || '0'),
      occupancy: 0,
      status: 'operational',
      temperature: 70,
      humidity: 45,
      energyUsage: 2000,
      airQuality: 'Good',
      utilization: 85,
      hvacHealth: 100,
      electricalHealth: 100,
      waterHealth: 100,
      fireSafetyHealth: 100,
      fileCount: 0
    };

    const originalBuildings = [...buildings];
    setBuildings(prev => [...prev, optimisticBuilding].sort((a, b) => a.name.localeCompare(b.name)));
    setSelectedBuilding(tempId);
    handleCloseDropdown();

    try {
      const response = await fetch(`${API_URL}/_api/buildings`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });

      if (response.ok) {
        const createdBuilding = await response.json();
        const mappedBuilding = {
          ...createdBuilding,
          floors: String(createdBuilding.floors || 0),
          sqft: String(createdBuilding.sqft || 0),
          occupancy: 0,
          status: 'operational',
          hvacHealth: 100,
          electricalHealth: 100,
          waterHealth: 100,
          fireSafetyHealth: 100,
          temperature: createdBuilding.temperature || 70,
          humidity: createdBuilding.humidity || 45,
          energyUsage: createdBuilding.energyUsage || 2000,
          airQuality: createdBuilding.airQuality || 'Good',
          utilization: createdBuilding.utilization || 85,
          fileCount: 0
        };

        // Replace temp with real
        setBuildings(prev => prev.map(b => b.id === tempId ? mappedBuilding : b));
        setSelectedBuilding(createdBuilding.id);

        // Reset form
        setNewBuilding({ name: '', address: '', city: '', state: '', country: '', floors: '', sqft: '' });
      } else {
        const errorData = await response.json();
        console.error('Error creating building:', response.statusText);
        alert(`Failed to create building: ${errorData.detail || response.statusText}`);
        // Revert
        setBuildings(prev => prev.filter(b => b.id !== tempId));
        if (selectedBuilding === tempId && originalBuildings.length > 0) {
          setSelectedBuilding(originalBuildings[0].id);
        }
      }
    } catch (error) {
      console.error('Error creating building:', error);
      alert('Failed to connect to the server.');
      // Revert
      setBuildings(prev => prev.filter(b => b.id !== tempId));
      if (selectedBuilding === tempId && originalBuildings.length > 0) {
        setSelectedBuilding(originalBuildings[0].id);
      }
    }
  };


  // Show empty state if no buildings found
  if (buildings.length === 0) {
    return (
      <div className="p-8 max-w-7xl mx-auto">
        <div className="flex flex-col items-center justify-center h-64 text-center">
          <Building className="w-12 h-12 text-gray-400 mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No Buildings Found</h3>
          <p className="text-gray-600 mb-4">Get started by adding your first building.</p>
          <button
            onClick={() => setShowAddBuilding(true)}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
          >
            Add Building
          </button>
          {showAddBuilding && (
            <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
              <div className="bg-white p-6 rounded-lg w-full max-w-md">
                <h4 className="text-gray-900 mb-4 text-lg font-medium">Add New Building</h4>
                {/* Form (Simplified inline for empty state special case if needed, or re-use same logic) */}
                {/* Actually, let's keep it consistent. The main return has the form too. */}
                {/* For now, just a button to toggle state is fine, the main render will handle the form overlay? 
                       Wait, the original code had the form INSIDE the sidebar under "Quick Actions".
                       This empty state seems to want a modal.
                       Let's just show the button here and let the user click it to toggle the form.
                       BUT, if buildings is empty, the main return is skipped.
                       So we DO need the form here or a way to get out of empty state.
                   */}

                {/* Let's replicate the form here properly so they can actually add it while in empty state */}
                <div className="space-y-3">
                  <div>
                    <label htmlFor="modal-name" className="block text-sm text-gray-600 mb-1">Building Name</label>
                    <input
                      id="modal-name"
                      type="text"
                      placeholder="e.g., Tower C"
                      value={newBuilding.name}
                      onChange={(e) => setNewBuilding(prev => ({ ...prev, name: e.target.value }))}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                    />
                  </div>
                  <div>
                    <label htmlFor="modal-address" className="block text-sm text-gray-600 mb-1">Building Address</label>
                    <input
                      id="modal-address"
                      type="text"
                      placeholder="e.g., 300 Main Street"
                      value={newBuilding.address}
                      onChange={(e) => setNewBuilding(prev => ({ ...prev, address: e.target.value }))}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label htmlFor="modal-city" className="block text-sm text-gray-600 mb-1">City</label>
                      <input
                        id="modal-city"
                        type="text"
                        placeholder="e.g. New York"
                        value={newBuilding.city}
                        onChange={(e) => setNewBuilding(prev => ({ ...prev, city: e.target.value }))}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                      />
                    </div>
                    <div>
                      <label htmlFor="modal-state" className="block text-sm text-gray-600 mb-1">State / Country</label>
                      <div className="flex gap-2">
                        <input
                          id="modal-state"
                          type="text"
                          placeholder="State"
                          value={newBuilding.state}
                          onChange={(e) => setNewBuilding(prev => ({ ...prev, state: e.target.value }))}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                        />
                        <input
                          id="modal-country"
                          type="text"
                          placeholder="Country"
                          value={newBuilding.country}
                          onChange={(e) => setNewBuilding(prev => ({ ...prev, country: e.target.value }))}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                        />
                      </div>
                    </div>
                  </div>
                  <div>
                    <label htmlFor="modal-floors" className="block text-sm text-gray-600 mb-1">Number of Floors</label>
                    <input
                      id="modal-floors"
                      type="number"
                      placeholder="e.g., 10"
                      value={newBuilding.floors}
                      onChange={(e) => setNewBuilding(prev => ({ ...prev, floors: e.target.value }))}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                    />
                  </div>
                  <div>
                    <label htmlFor="modal-sqft" className="block text-sm text-gray-600 mb-1">Square Footage</label>
                    <input
                      id="modal-sqft"
                      type="text"
                      placeholder="e.g., 250,000"
                      value={newBuilding.sqft}
                      onChange={(e) => setNewBuilding(prev => ({ ...prev, sqft: e.target.value }))}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                    />
                  </div>
                  <div className="flex gap-2 pt-2">
                    <button
                      type="button"
                      onClick={() => setShowAddBuilding(false)}
                      className="flex-1 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors text-sm"
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      onClick={handleAddBuilding}
                      className="flex-1 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm"
                    >
                      Add Building
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  if (!selected) {
    // Should not happen if buildings.length > 0 due to the selection logic, but just in case
    return null;
  }

  const handleCloseDeleteDropdown = () => {
    setShowDeleteBuilding(false);
    setDeleteSearchQuery('');
  };

  const handleDeleteBuilding = async (buildingId: string) => {
    // Optimistic Update: Remove from UI immediately
    const originalBuildings = [...buildings];
    setBuildings(prev => prev.filter(b => b.id !== buildingId));

    // Switch selection if needed (optimistically)
    if (selectedBuilding === buildingId) {
      const remainingBuildings = buildings.filter(b => b.id !== buildingId);
      if (remainingBuildings.length > 0) {
        setSelectedBuilding(remainingBuildings[0].id);
      }
    }
    handleCloseDeleteDropdown(); // Close UI immediately

    try {
      const response = await fetch(`${API_URL}/_api/buildings/${buildingId}`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        // Revert on failure
        setBuildings(originalBuildings);
        console.error('Error deleting building:', response.statusText);
        alert('Failed to delete building');
      }
    } catch (error) {
      // Revert on error
      setBuildings(originalBuildings);
      console.error('Error deleting building:', error);
      alert('Failed to connect to the server.');
    }
  };

  const filteredBuildingsForDelete = buildings.filter(building => {
    const query = deleteSearchQuery.toLowerCase();
    return building.name.toLowerCase().includes(query) ||
      building.address.toLowerCase().includes(query);
  });

  return (
    <div className="p-8 max-w-7xl mx-auto">
      <div className="mb-8">
        <h1 className="text-gray-900 mb-2">Building Management</h1>
        <p className="text-gray-600">
          Monitor and manage all your properties in real-time
        </p>
      </div>

      <div className="grid grid-cols-3 gap-4 mb-8">
        {buildingsWithDefaults.map((building) => (
          <button
            key={building.id}
            onClick={() => setSelectedBuilding(building.id)}
            className={`text - left p - 6 rounded - xl border - 2 transition - all ${selectedBuilding === building.id
              ? 'border-blue-500 bg-blue-50'
              : 'border-gray-200 bg-white hover:border-gray-300'
              } `}
          >
            <div className="flex items-start justify-between mb-3">
              <div className="w-12 h-12 bg-blue-100 rounded-lg flex items-center justify-center">
                <Building className="w-6 h-6 text-blue-600" />
              </div>
              {building.alerts > 0 && (
                <span className="px-2 py-1 bg-red-100 text-red-700 rounded-full text-xs">
                  {building.alerts} alerts
                </span>
              )}
            </div>
            <h3 className="text-gray-900 mb-1">{building.name}</h3>
            <p className="text-sm text-gray-600 mb-3">{building.address}</p>
            <div className="flex items-center gap-4 text-sm text-gray-600">
              <span>{building.floors} floors</span>
              <span>•</span>
              <span>{Number(building.sqft.replace(/,/g, '')).toLocaleString()} sq ft</span>
            </div>
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <div className="grid grid-cols-4 gap-4">
            <div className="bg-white rounded-xl p-4 border border-gray-200">
              <div className="flex items-center gap-2 mb-2">
                <ThermometerSun className="w-5 h-5 text-orange-600" />
                <span className="text-sm text-gray-600">Temperature</span>
              </div>
              <div className="text-gray-900 font-medium">N/A</div>
            </div>
            <div className="bg-white rounded-xl p-4 border border-gray-200">
              <div className="flex items-center gap-2 mb-2">
                <Droplets className="w-5 h-5 text-blue-600" />
                <span className="text-sm text-gray-600">Humidity</span>
              </div>
              <div className="text-gray-900 font-medium">N/A</div>
            </div>
            <div className="bg-white rounded-xl p-4 border border-gray-200">
              <div className="flex items-center gap-2 mb-2">
                <Wind className="w-5 h-5 text-green-600" />
                <span className="text-sm text-gray-600">Air Quality</span>
              </div>
              <div className="text-gray-900 font-medium">N/A</div>
            </div>
            <div className="bg-white rounded-xl p-4 border border-gray-200">
              <div className="flex items-center gap-2 mb-2">
                <Zap className="w-5 h-5 text-yellow-600" />
                <span className="text-sm text-gray-600">Energy</span>
              </div>
              <div className="text-gray-900 font-medium">N/A</div>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <h2 className="text-gray-900 mb-4">Building Systems</h2>
            <div className="space-y-4">
              {details.systems.map((system, index) => {
                const Icon = system.icon;
                return (
                  <div key={index} className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
                    <div className="flex items-center gap-4 flex-1">
                      <div className={`w - 10 h - 10 rounded - lg flex items - center justify - center ${system.status === 'good' ? 'bg-green-100' :
                        system.status === 'warning' ? 'bg-orange-100' :
                          'bg-red-100'
                        } `}>
                        <Icon className={`w - 5 h - 5 ${system.status === 'good' ? 'text-green-600' :
                          system.status === 'warning' ? 'text-orange-600' :
                            'text-red-600'
                          } `} />
                      </div>
                      <div className="flex-1">
                        <div className="flex items-center gap-2 mb-1">
                          <h3 className="text-gray-900">{system.name}</h3>
                          {system.status === 'good' && (
                            <span className="px-2 py-1 bg-green-100 text-green-700 rounded text-xs">
                              Operational
                            </span>
                          )}
                          {system.status === 'warning' && (
                            <span className="px-2 py-1 bg-orange-100 text-orange-700 rounded text-xs">
                              Needs Attention
                            </span>
                          )}
                        </div>
                        {system.alert && (
                          <p className="text-sm text-gray-600">{system.alert}</p>
                        )}
                      </div>
                      <div className="text-gray-900">{system.value}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* <div className="bg-white rounded-xl border border-gray-200 p-6">
            <h2 className="text-gray-900 mb-4">Recent Issues</h2>
            <div className="space-y-3">
              {details.recentIssues.map((issue, index) => (
                <div key={index} className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
                  <div className="flex items-center gap-3 flex-1">
                    <div className={`w - 1 h - 12 rounded - full ${
  issue.priority === 'high' ? 'bg-red-500' :
  issue.priority === 'medium' ? 'bg-orange-500' :
    'bg-blue-500'
} `} />
                    <div className="flex-1">
                      <h3 className="text-gray-900 mb-1">Floor {issue.floor}: {issue.issue}</h3>
                      <p className="text-sm text-gray-600">{issue.time}</p>
                    </div>
                    <span className={`px - 3 py - 1 rounded - full text - xs ${
  issue.priority === 'high' ? 'bg-red-100 text-red-700' :
  issue.priority === 'medium' ? 'bg-orange-100 text-orange-700' :
    'bg-blue-100 text-blue-700'
} `}>
                      {issue.priority}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div> */}
        </div>

        <div className="space-y-6">
          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <h3 className="text-gray-900 mb-4">Building Information</h3>
            <div className="space-y-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-gray-600">Total Floors</span>
                <span className="text-gray-900">{selected.floors}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-gray-600">Square Footage</span>
                <span className="text-gray-900">{Number(selected.sqft.replace(/,/g, '')).toLocaleString()} sq ft</span>
              </div>
            </div>
          </div>

          {/* <div className="bg-white rounded-xl border border-gray-200 p-6">
            <h3 className="text-gray-900 mb-4">Upcoming Maintenance</h3>
            <div className="space-y-3">
              {details.upcomingMaintenance.map((item, index) => (
                <div key={index} className="pb-3 border-b border-gray-200 last:border-0 last:pb-0">
                  <div className="flex items-start justify-between gap-2 mb-1">
                    <h4 className="text-sm text-gray-900">{item.task}</h4>
                    <span className={`px - 2 py - 1 rounded text - xs whitespace - nowrap ${
  item.type === 'Required' ? 'bg-red-100 text-red-700' :
  'bg-blue-100 text-blue-700'
} `}>
                      {item.type}
                    </span>
                  </div>
                  <p className="text-xs text-gray-600">{item.date}</p>
                </div>
              ))}
            </div>
          </div> */}

          <div className="bg-gradient-to-br from-blue-50 to-purple-50 rounded-xl border border-blue-200 p-6">
            <h3 className="text-gray-900 mb-4">Quick Actions</h3>
            <div className="space-y-2">
              <button
                type="button"
                onClick={() => setShowAddBuilding(!showAddBuilding)}
                className="w-full py-2 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors text-sm"
              >
                Add Building
              </button>
              {showAddBuilding && (
                <div className="bg-white border border-gray-200 rounded-lg p-4">
                  <h4 className="text-gray-900 mb-4 text-sm font-medium">Add New Building</h4>
                  <div className="space-y-3">
                    <div>
                      <label htmlFor="building-name" className="block text-sm text-gray-600 mb-1">Building Name</label>
                      <input
                        id="building-name"
                        type="text"
                        placeholder="e.g., Tower C"
                        value={newBuilding.name}
                        onChange={(e) => handleInputChange('name', e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                      />
                    </div>
                    <div>
                      <label htmlFor="building-address" className="block text-sm text-gray-600 mb-1">Building Address</label>
                      <input
                        id="building-address"
                        type="text"
                        placeholder="e.g., 300 Main Street"
                        value={newBuilding.address}
                        onChange={(e) => handleInputChange('address', e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label htmlFor="building-city" className="block text-sm text-gray-600 mb-1">City</label>
                        <input
                          id="building-city"
                          type="text"
                          placeholder="e.g. New York"
                          value={newBuilding.city}
                          onChange={(e) => handleInputChange('city', e.target.value)}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                        />
                      </div>
                      <div>
                        <label htmlFor="building-state" className="block text-sm text-gray-600 mb-1">State / Country</label>
                        <div className="flex gap-2">
                          <input
                            id="building-state"
                            type="text"
                            placeholder="State"
                            value={newBuilding.state}
                            onChange={(e) => handleInputChange('state', e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                          />
                          <input
                            id="building-country"
                            type="text"
                            placeholder="Country"
                            value={newBuilding.country}
                            onChange={(e) => handleInputChange('country', e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                          />
                        </div>
                      </div>
                    </div>
                    <div>
                      <label htmlFor="building-floors" className="block text-sm text-gray-600 mb-1">Number of Floors</label>
                      <input
                        id="building-floors"
                        type="number"
                        placeholder="e.g., 10"
                        value={newBuilding.floors}
                        onChange={(e) => handleInputChange('floors', e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                      />
                    </div>
                    <div>
                      <label htmlFor="building-sqft" className="block text-sm text-gray-600 mb-1">Square Footage</label>
                      <input
                        id="building-sqft"
                        type="text"
                        placeholder="e.g., 250,000"
                        value={newBuilding.sqft}
                        onChange={(e) => handleInputChange('sqft', e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                      />
                    </div>
                    <div className="flex gap-2 pt-2">
                      <button
                        type="button"
                        onClick={handleCloseDropdown}
                        className="flex-1 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors text-sm"
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        onClick={handleAddBuilding}
                        className="flex-1 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm"
                      >
                        Add Building
                      </button>
                    </div>
                  </div>
                </div>
              )}
              <button
                type="button"
                onClick={() => setShowDeleteBuilding(!showDeleteBuilding)}
                className="w-full py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors text-sm"
              >
                Delete Building
              </button>
              {showDeleteBuilding && (
                <div className="bg-white border border-gray-200 rounded-lg p-4 mt-2">
                  <h4 className="text-gray-900 mb-4 text-sm font-medium">Delete Building</h4>
                  <div className="space-y-3">
                    <div>
                      <label htmlFor="delete-search" className="block text-sm text-gray-600 mb-1">Search by Name or Address</label>
                      <input
                        id="delete-search"
                        type="text"
                        placeholder="Search buildings..."
                        value={deleteSearchQuery}
                        onChange={(e) => setDeleteSearchQuery(e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 text-sm"
                      />
                    </div>
                    {deleteSearchQuery && filteredBuildingsForDelete.length > 0 && (
                      <div className="space-y-2 max-h-48 overflow-y-auto">
                        {filteredBuildingsForDelete.map((building) => (
                          <div
                            key={building.id}
                            className="flex items-center justify-between p-3 bg-gray-50 rounded-lg border border-gray-200"
                          >
                            <div className="mr-2 overflow-hidden">
                              <div className="text-sm font-medium text-gray-900 truncate">{building.name}</div>
                              <div className="text-xs text-gray-600 truncate">{building.address}</div>
                            </div>
                            <button
                              type="button"
                              onClick={() => handleDeleteBuilding(building.id)}
                              className="px-3 py-1 bg-red-600 text-white rounded hover:bg-red-700 transition-colors text-sm whitespace-nowrap"
                            >
                              Delete
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                    {deleteSearchQuery && filteredBuildingsForDelete.length === 0 && (
                      <div className="text-sm text-gray-500 text-center py-4">
                        No buildings found matching your search.
                      </div>
                    )}
                    {!deleteSearchQuery && (
                      <div className="text-sm text-gray-500 text-center py-4">
                        Enter a building name or address to search.
                      </div>
                    )}
                    <div className="flex gap-2 pt-2">
                      <button
                        type="button"
                        onClick={handleCloseDeleteDropdown}
                        className="flex-1 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors text-sm"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* <button className="w-full py-2 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors text-sm">
                Create Work Order
              </button>
              <button className="w-full py-2 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors text-sm">
                View Floor Plans
              </button>
              <button className="w-full py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm">
                Ask AI About Building
              </button> */}
            </div>
          </div>
        </div>
      </div>

    </div>
  );
}
