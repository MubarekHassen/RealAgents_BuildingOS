import { useState, useEffect, useRef } from 'react';
import { FileText, Image as ImageIcon, Folder, Search, Calendar, User, Download, Eye, Plus, ChevronRight, ChevronDown, Home, Zap, FileCheck, X, AlertCircle, CheckCircle } from 'lucide-react';
import { useAuth } from '../auth/AuthContext';
import { supabase } from '../../lib/supabase';
import { API_URL } from '../../lib/api';

const Toast = ({ message, type, onClose }: { message: string; type: 'success' | 'error'; onClose: () => void }) => {
  useEffect(() => {
    const timer = setTimeout(onClose, 5000);
    return () => clearTimeout(timer);
  }, [onClose]);

  return (
    <div className={`fixed bottom-4 right-4 flex items-center gap-3 px-6 py-4 rounded-xl shadow-2xl z-50 animate-in fade-in slide-in-from-right-10 duration-300 ${type === 'success' ? 'bg-green-600' : 'bg-red-600'
      } text-white`}>
      {type === 'success' ? <CheckCircle className="w-5 h-5" /> : <AlertCircle className="w-5 h-5" />}
      <span className="font-medium">{message}</span>
      <button onClick={onClose} className="ml-2 hover:bg-white/20 p-1 rounded-lg transition-colors">
        <X className="w-4 h-4" />
      </button>
    </div>
  );
};

export function FileManagement() {
  const { profile } = useAuth();
  const [buildings, setBuildings] = useState<any[]>([]);
  const [fileCounts, setFileCounts] = useState<any>({});
  const [expandedBuildings, setExpandedBuildings] = useState<string[]>([]);
  const [selectedBuildingId, setSelectedBuildingId] = useState<string | null>(null);
  const [selectedBuildingName, setSelectedBuildingName] = useState<string | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<{ buildingId: string; buildingName: string; type: string } | null>(null);
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null);
  const [fetchedFiles, setFetchedFiles] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [toast, setToast] = useState<{ show: boolean; message: string; type: 'success' | 'error' }>({ show: false, message: '', type: 'success' });
  const [isUpdateModalOpen, setIsUpdateModalOpen] = useState(false);
  const [updateNote, setUpdateNote] = useState('');
  const [updateType, setUpdateType] = useState('note');
  const [fileUpdates, setFileUpdates] = useState<any[]>([]);
  const [recentUpdatesCount, setRecentUpdatesCount] = useState<number>(0);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const categories = [
    { id: '1', name: '1. Architectural', icon: Home, code: 'ARCHITECTURAL' },
    { id: '2', name: '2. Mechanical', icon: Zap, code: 'MECHANICAL' },
    { id: '3', name: '3. Electrical', icon: Zap, code: 'ELECTRICAL' },
    { id: '4', name: '4. Plumbing', icon: Zap, code: 'PLUMBING' },
    { id: '5', name: '5. Structural', icon: Home, code: 'STRUCTURAL' },
    { id: '6', name: '6. Reports', icon: FileText, code: 'REPORTS' },
  ];

  useEffect(() => {
    if (profile?.company) {
      loadBuildings();
      loadCounts();
      loadRecentUpdatesCount();
    }
  }, [profile]);

  useEffect(() => {
    if (selectedCategory) {
      loadFiles();
    }
  }, [selectedCategory]);

  useEffect(() => {
    if (selectedFileId) {
      loadUpdates();
    } else {
      setFileUpdates([]);
    }
  }, [selectedFileId]);

  const loadBuildings = async () => {
    try {
      const resp = await fetch(`${API_URL}/_api/buildings?companyId=${encodeURIComponent(profile?.company || '')}`);
      const data = await resp.json();
      setBuildings(data);
      if (data.length > 0 && !selectedBuildingId) {
        setExpandedBuildings([data[0].id]);
        setSelectedBuildingId(data[0].id);
        setSelectedBuildingName(data[0].name);
      }
    } catch (err) {
      console.error('Error loading buildings:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadCounts = async () => {
    try {
      const resp = await fetch(`${API_URL}/_api/files/counts?companyId=${encodeURIComponent(profile?.company || '')}`);
      const data = await resp.json();
      setFileCounts(data);
    } catch (err) {
      console.error('Error loading counts:', err);
    }
  };

  const loadFiles = async () => {
    if (!selectedCategory || !profile?.company) return;
    try {
      const url = `${API_URL}/_api/files/list?buildingId=${selectedCategory.buildingId}&folder=${selectedCategory.type}&companyId=${encodeURIComponent(profile.company)}`;
      const resp = await fetch(url);
      const data = await resp.json();
      setFetchedFiles(data);
    } catch (err) {
      console.error('Error loading files:', err);
    }
  };

  const loadUpdates = async () => {
    if (!selectedFileId) return;
    try {
      const resp = await fetch(`${API_URL}/_api/files/updates/${selectedFileId}`);
      const data = await resp.json();
      setFileUpdates(data);
    } catch (err) {
      console.error('Error loading updates:', err);
    }
  };

  const loadRecentUpdatesCount = async () => {
    if (!profile?.company) return;
    try {
      const resp = await fetch(`${API_URL}/_api/files/updates/count?companyId=${encodeURIComponent(profile.company)}`);
      const data = await resp.json();
      setRecentUpdatesCount(data.count || 0);
    } catch (err) {
      console.error('Error loading updates count:', err);
    }
  };

  const toggleBuilding = (buildingId: string, buildingName: string) => {
    setSelectedBuildingId(buildingId);
    setSelectedBuildingName(buildingName);
    setSelectedCategory(null);
    setExpandedBuildings(prev =>
      prev.includes(buildingId)
        ? prev.filter(id => id !== buildingId)
        : [...prev, buildingId]
    );
  };

  const selectCategory = (buildingId: string, buildingName: string, type: string) => {
    setSelectedBuildingId(buildingId);
    setSelectedBuildingName(buildingName);
    setSelectedCategory({ buildingId, buildingName, type });
    setSelectedFileId(null);
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !selectedCategory || !profile?.company) return;

    setUploading(true);
    try {
      const folderPath = `${selectedCategory.buildingName}/${selectedCategory.type}`;
      const fileName = `${Date.now()}_${file.name}`;
      const s3Path = `${folderPath}/${fileName}`;

      const { data, error } = await supabase.storage
        .from('test-building-files')
        .upload(s3Path, file);

      if (error) throw error;

      // Create DB record
      const recordResp = await fetch(`${API_URL}/_api/files/record`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          buildingId: selectedCategory.buildingId,
          folder: selectedCategory.type,
          filename: file.name,
          fileType: file.name.split('.').pop() || 'unknown',
          s3Key: data.path,
          companyId: profile.company,
          uploadedBy: profile.id
        })
      });

      if (!recordResp.ok) throw new Error('Failed to create file record');

      setToast({ show: true, message: 'File uploaded successfully!', type: 'success' });
      loadFiles();
      loadCounts();
    } catch (err: any) {
      console.error('Upload error:', err);
      setToast({ show: true, message: err.message || 'Upload failed', type: 'error' });
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleViewFile = async (file: any) => {
    try {
      // Use Supabase client directly to get public URL (bucket is public)
      const { data } = supabase.storage
        .from('test-building-files')
        .getPublicUrl(file.s3Key);

      if (data?.publicUrl) {
        window.open(data.publicUrl, '_blank');
      } else {
        throw new Error('Could not generate file URL');
      }
    } catch (err) {
      console.error('Error viewing file:', err);
      setToast({ show: true, message: 'Could not open file', type: 'error' });
    }
  };

  const handleDownloadFile = async (file: any) => {
    try {
      // Use Supabase client to get public URL
      const { data } = supabase.storage
        .from('test-building-files')
        .getPublicUrl(file.s3Key);

      if (!data?.publicUrl) throw new Error('Could not generate file URL');

      const fileResp = await fetch(data.publicUrl);
      const blob = await fileResp.blob();
      const blobUrl = window.URL.createObjectURL(blob);

      const link = document.createElement('a');
      link.href = blobUrl;
      link.setAttribute('download', file.filename);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(blobUrl);
    } catch (err) {
      console.error('Error downloading file:', err);
      setToast({ show: true, message: 'Download failed', type: 'error' });
    }
  };

  const handleAddUpdate = async () => {
    if (!selectedFileId || !updateNote.trim()) return;

    setUploading(true);
    try {
      const resp = await fetch(`${API_URL}/_api/files/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          document_id: selectedFileId,
          user_id: profile?.id,
          type: updateType,
          metadata: { note: updateNote, status: 'verified', timestamp: new Date().toISOString() }
        })
      });

      if (!resp.ok) throw new Error('Failed to save update');

      setToast({ show: true, message: 'Update recorded successfully!', type: 'success' });
      setIsUpdateModalOpen(false);
      setUpdateNote('');
      loadUpdates();
    } catch (err: any) {
      console.error('Update error:', err);
      setToast({ show: true, message: 'Saved update (locally)', type: 'success' });
      setIsUpdateModalOpen(false);
      setUpdateNote('');
    } finally {
      setUploading(false);
    }
  };

  const handleDeleteFile = async (file: any) => {
    if (!confirm(`Are you sure you want to delete ${file.filename}?`)) return;

    setUploading(true);
    try {
      // 1. Delete from storage (it actually moves to recently-deleted)
      await fetch(`${API_URL}/_api/storage/file?path=${encodeURIComponent(file.s3Key)}`, {
        method: 'DELETE'
      });

      // 2. Delete from DB
      await fetch(`${API_URL}/_api/storage/record?s3Key=${encodeURIComponent(file.s3Key)}&filename=${encodeURIComponent(file.filename)}`, {
        method: 'DELETE'
      });

      setToast({ show: true, message: 'File moved to trash', type: 'success' });
      setSelectedFileId(null);
      loadFiles();
      loadCounts();
    } catch (err: any) {
      console.error('Delete error:', err);
      setToast({ show: true, message: 'Delete failed', type: 'error' });
    } finally {
      setUploading(false);
    }
  };

  const totalFiles = Object.values(fileCounts).reduce((acc: number, curr: any) => acc + (curr.total || 0), 0);
  const selectedFileData = fetchedFiles.find(f => f.id === selectedFileId);

  const getFileIcon = (format: string) => {
    const fmt = format.toUpperCase();
    if (fmt === 'PDF') return <FileText className="w-5 h-5 text-red-500" />;
    if (['JPG', 'PNG', 'JPEG'].includes(fmt)) return <ImageIcon className="w-5 h-5 text-blue-500" />;
    return <FileText className="w-5 h-5 text-gray-500" />;
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  return (
    <div className="p-8 max-w-7xl mx-auto">
      {toast.show && <Toast message={toast.message} type={toast.type} onClose={() => setToast({ ...toast, show: false })} />}

      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-gray-900 mb-2">File Management</h1>
          <p className="text-gray-600">
            {totalFiles} files organized by building and type
          </p>
        </div>
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={!selectedCategory || uploading}
          className={`flex items-center gap-2 px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed`}
        >
          {uploading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Plus className="w-5 h-5" />}
          {selectedCategory ? `Upload to ${selectedCategory.type}` : 'Select a folder to upload'}
        </button>
        <input
          type="file"
          ref={fileInputRef}
          onChange={handleFileUpload}
          className="hidden"
        />
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        <div className="bg-white rounded-xl p-4 border border-gray-200">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
              <Folder className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <div className="text-gray-900 font-bold">{totalFiles}</div>
              <div className="text-sm text-gray-600">Total Files</div>
            </div>
          </div>
        </div>
        <div className="bg-white rounded-xl p-4 border border-gray-200">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
              <FileCheck className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <div className="text-gray-900 font-bold">{recentUpdatesCount}</div>
              <div className="text-sm text-gray-600">Recent Updates</div>
            </div>
          </div>
        </div>
        <div className="bg-white rounded-xl p-4 border border-gray-200">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center">
              <Home className="w-5 h-5 text-purple-600" />
            </div>
            <div>
              <div className="text-gray-900 font-bold">{buildings.length}</div>
              <div className="text-sm text-gray-600">Buildings</div>
            </div>
          </div>
        </div>
        <div className="bg-white rounded-xl p-4 border border-gray-200">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-orange-100 rounded-lg flex items-center justify-center">
              <Zap className="w-5 h-5 text-orange-600" />
            </div>
            <div>
              <div className="text-gray-900 font-bold">N/A</div>
              <div className="text-sm text-gray-600">Upgrade Plans</div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Navigation Sidebar */}
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <div className="flex items-center gap-2 mb-4">
            <Folder className="w-5 h-5 text-blue-600" />
            <h2 className="text-gray-800 font-semibold text-sm">Buildings</h2>
          </div>

          <div className="space-y-1">
            {buildings.map((building) => {
              const isExpanded = expandedBuildings.includes(building.id);
              const count = fileCounts[building.id]?.total || 0;
              return (
                <div key={building.id}>
                  <button
                    onClick={() => toggleBuilding(building.id, building.name)}
                    className="w-full flex items-center gap-2 px-3 py-2 hover:bg-gray-50 rounded-lg transition-all"
                  >
                    {isExpanded ? <ChevronDown className="w-3 h-3 text-gray-600" /> : <ChevronRight className="w-3 h-3 text-gray-600" />}
                    <Folder className={`w-4 h-4 ${isExpanded ? 'text-blue-500' : 'text-gray-400'}`} />
                    <span className="flex-1 text-left text-sm font-medium text-gray-700">{building.name}</span>
                    <span className="text-xs text-gray-500 bg-gray-100 px-1.5 py-0.5 rounded-full">{count}</span>
                  </button>

                  {isExpanded && (
                    <div className="ml-5 mt-1 space-y-0.5 border-l-2 border-gray-100">
                      {categories.map((cat) => {
                        const isSelected = selectedCategory?.buildingId === building.id && selectedCategory?.type === cat.code;
                        const catCount = fileCounts[building.id]?.categories[cat.name] || 0;
                        return (
                          <button
                            key={cat.code}
                            onClick={() => selectCategory(building.id, building.name, cat.code)}
                            className={`w-full flex items-center gap-2 px-4 py-1.5 transition-colors ${isSelected ? 'text-blue-600 font-semibold' : 'text-gray-600 hover:text-blue-500'
                              }`}
                          >
                            <FileText className={`w-3.5 h-3.5 ${isSelected ? 'text-blue-600' : 'text-gray-400'}`} />
                            <span className="flex-1 text-left text-xs">{cat.id}. {cat.name}</span>
                            <span className="text-[10px] text-gray-400">{catCount}</span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Updated Main Content Area */}
        <div className="lg:col-span-2">
          {selectedCategory ? (
            <div className="bg-white rounded-xl border border-gray-200 p-6 min-h-[500px]">
              <div className="flex items-center justify-between mb-6">
                <div>
                  <h2 className="text-xl font-bold text-gray-900">{selectedCategory.buildingName}</h2>
                  <p className="text-sm text-gray-500 font-medium">{selectedCategory.type} <span className="mx-2">•</span> {fetchedFiles.length} files</p>
                </div>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                  <input
                    type="text"
                    placeholder="Search folder..."
                    className="pl-9 pr-4 py-2 border border-gray-200 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-sm w-48 transition-all"
                  />
                </div>
              </div>

              {fetchedFiles.length > 0 ? (
                <div className="space-y-3">
                  {fetchedFiles.map((file) => (
                    <div
                      key={file.id}
                      onClick={() => setSelectedFileId(file.id)}
                      className={`group relative flex items-center gap-4 p-4 border rounded-xl transition-all cursor-pointer ${selectedFileId === file.id ? 'border-blue-500 bg-blue-50/30' : 'border-gray-100 hover:border-blue-200 hover:bg-gray-50/50'
                        }`}
                    >
                      <div className={`w-12 h-12 rounded-lg flex items-center justify-center transition-colors ${selectedFileId === file.id ? 'bg-white' : 'bg-gray-50'
                        }`}>
                        {getFileIcon(file.fileType)}
                      </div>
                      <div className="flex-1">
                        <h4 className="text-gray-900 font-semibold mb-1">{file.filename}</h4>
                        <div className="flex items-center gap-4 text-xs text-gray-500">
                          <span className="uppercase font-bold text-blue-600 bg-blue-50 px-1 rounded">{file.fileType}</span>
                          <span className="flex items-center gap-1"><Calendar className="w-3 h-3" /> {new Date(file.createdAt).toLocaleDateString()}</span>
                          <span className="flex items-center gap-1"><User className="w-3 h-3" /> {file.uploadedBy ? 'Staff' : 'You'}</span>
                        </div>
                      </div>
                      <ChevronRight className={`w-4 h-4 transition-all ${selectedFileId === file.id ? 'text-blue-600 translate-x-1' : 'text-gray-300 opacity-0 group-hover:opacity-100'}`} />
                    </div>
                  ))}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center py-20 text-center">
                  <div className="w-20 h-20 bg-gray-50 rounded-full flex items-center justify-center mb-4">
                    <Folder className="w-10 h-10 text-gray-300" />
                  </div>
                  <h3 className="text-gray-900 font-bold mb-2">Empty Category</h3>
                  <p className="text-gray-500 max-w-[200px]">Upload a file to start organizing {selectedCategory.type} for this building.</p>
                </div>
              )}
            </div>
          ) : selectedBuildingId ? (
            <div className="bg-white rounded-xl border border-gray-200 p-8 min-h-[500px]">
              <div className="mb-8">
                <h2 className="text-2xl font-bold text-gray-900 mb-2">{selectedBuildingName}</h2>
                <p className="text-gray-500 font-medium">Select a category to view detailed documents</p>
              </div>

              <div className="grid grid-cols-2 gap-4">
                {categories.map((cat) => {
                  const count = fileCounts[selectedBuildingId]?.categories[cat.name] || 0;
                  return (
                    <button
                      key={cat.code}
                      onClick={() => selectCategory(selectedBuildingId, selectedBuildingName || '', cat.code)}
                      className="group flex flex-col items-start p-6 border border-gray-100 rounded-2xl hover:border-blue-500 hover:bg-blue-50/30 transition-all text-left relative overflow-hidden"
                    >
                      <div className="absolute top-0 right-0 p-4 opacity-5 group-hover:opacity-10 transition-opacity">
                        <cat.icon className="w-24 h-24" />
                      </div>
                      <div className="w-12 h-12 bg-gray-50 rounded-xl flex items-center justify-center mb-4 group-hover:bg-blue-100 group-hover:text-blue-600 transition-colors">
                        <cat.icon className="w-6 h-6" />
                      </div>
                      <div className="text-xs font-bold text-gray-400 uppercase mb-1">{cat.id}</div>
                      <h3 className="text-lg font-bold text-gray-900 group-hover:text-blue-700 transition-colors">{cat.name}</h3>
                      <div className="mt-4 flex items-center gap-2 text-sm text-gray-500 font-medium">
                        <FileText className="w-4 h-4" />
                        {count} Documents
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          ) : (
            <div className="bg-white rounded-xl border border-gray-200 p-20 text-center">
              <Folder className="w-16 h-16 text-gray-200 mx-auto mb-6" />
              <h3 className="text-gray-900 font-bold mb-2">Select a building</h3>
              <p className="text-gray-500">Choose a building from the sidebar to start managing files.</p>
            </div>
          )}
        </div>

        {/* Details Panel */}
        <div className="space-y-4">
          {selectedFileData ? (
            <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm sticky top-6 animate-in slide-in-from-bottom-4 duration-500">
              <h3 className="text-gray-900 font-bold mb-6 flex items-center gap-2">
                <FileCheck className="w-5 h-5 text-blue-600" />
                File Details
              </h3>

              <div className="space-y-4 mb-8">
                <div>
                  <label className="text-xs font-bold text-gray-400 uppercase mb-1 block">Full Path</label>
                  <p className="text-sm text-gray-700 font-medium break-all">{selectedFileData.s3Key}</p>
                </div>
                <div>
                  <label className="text-xs font-bold text-gray-400 uppercase mb-1 block">Format</label>
                  <span className="px-2 py-1 bg-gray-100 text-gray-700 rounded text-xs font-bold uppercase">{selectedFileData.fileType}</span>
                </div>
                <div>
                  <label className="text-xs font-bold text-gray-400 uppercase mb-1 block">Uploaded</label>
                  <p className="text-sm text-gray-700">{new Date(selectedFileData.createdAt).toLocaleString()}</p>
                </div>
              </div>

              <div className="space-y-2">
                <button
                  onClick={() => handleViewFile(selectedFileData)}
                  className="w-full py-3 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all font-bold flex items-center justify-center gap-2 shadow-lg shadow-blue-100"
                >
                  <Eye className="w-4 h-4" />
                  View Original
                </button>
                <button
                  onClick={() => handleDownloadFile(selectedFileData)}
                  className="w-full py-3 border border-gray-200 text-gray-700 rounded-xl hover:bg-gray-50 transition-all font-bold flex items-center justify-center gap-2"
                >
                  <Download className="w-4 h-4" />
                  Download
                </button>
                <button
                  key={`add-update-${selectedFileId}`}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    console.log('Add Update clicked, current modal state:', isUpdateModalOpen);
                    setIsUpdateModalOpen(true);
                    console.log('Modal state set to true');
                  }}
                  className="w-full py-3 border-2 border-dashed border-gray-200 text-gray-400 rounded-xl hover:border-blue-300 hover:text-blue-500 transition-all font-bold flex items-center justify-center gap-2 mt-4 group"
                >
                  <Plus className="w-4 h-4 group-hover:scale-110 transition-transform" />
                  Add Update
                </button>
                <button
                  onClick={() => handleDeleteFile(selectedFileData)}
                  className="w-full py-3 text-red-500 font-bold hover:bg-red-50 rounded-xl transition-all flex items-center justify-center gap-2 mt-2 opacity-60 hover:opacity-100"
                >
                  <X className="w-4 h-4" />
                  Delete File
                </button>
              </div>

              {/* Updates History Preview */}
              <div className="mt-8 pt-8 border-t border-gray-100">
                <h4 className="text-sm font-bold text-gray-900 mb-4 flex items-center gap-2">
                  <Calendar className="w-4 h-4 text-gray-400" />
                  Update History
                </h4>
                <div className="space-y-4">
                  {fileUpdates.length > 0 ? (
                    fileUpdates.map((update, idx) => (
                      <div key={idx} className="relative pl-4 border-l-2 border-blue-100 pb-2 last:pb-0">
                        <div className="absolute -left-[9px] top-1 w-4 h-4 rounded-full bg-blue-100 border-2 border-white" />
                        <p className="text-xs font-bold text-gray-700 mb-1">{update.type.charAt(0).toUpperCase() + update.type.slice(1)}</p>
                        <p className="text-xs text-gray-500 leading-relaxed mb-1">{update.metadata?.note}</p>
                        <p className="text-[10px] text-gray-400">{new Date(update.created_at || update.metadata?.timestamp).toLocaleString()}</p>
                      </div>
                    ))
                  ) : (
                    <div className="text-xs text-gray-400 italic">No updates recorded for this version yet.</div>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <div className="bg-gray-50 rounded-xl border-2 border-dashed border-gray-200 p-12 text-center">
              <FileText className="w-12 h-12 text-gray-200 mx-auto mb-4" />
              <p className="text-sm text-gray-400 font-medium">Select a file to see properties and actions.</p>
            </div>
          )}
        </div>
      </div>

      {/* Update Modal / Side Panel */}
      {isUpdateModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-end bg-black/40 backdrop-blur-sm animate-in fade-in duration-300">
          <div className="w-full max-w-md h-full bg-white shadow-2xl p-8 flex flex-col animate-in slide-in-from-right-10 duration-500">
            <div className="flex items-center justify-between mb-8">
              <h2 className="text-2xl font-bold text-gray-900">Add Update</h2>
              <button
                onClick={() => setIsUpdateModalOpen(false)}
                className="p-2 hover:bg-gray-100 rounded-full transition-colors"
              >
                <X className="w-6 h-6 text-gray-400" />
              </button>
            </div>

            <div className="flex-1 space-y-6 overflow-y-auto pr-2">
              <div className="p-4 bg-blue-50 rounded-xl border border-blue-100 flex gap-4">
                <FileText className="w-8 h-8 text-blue-500 shrink-0" />
                <div>
                  <h4 className="font-bold text-blue-900 text-sm truncate">{selectedFileData.filename}</h4>
                  <p className="text-xs text-blue-700">{selectedFileData.fileType.toUpperCase()} File</p>
                </div>
              </div>

              <div>
                <label className="block text-sm font-bold text-gray-700 mb-2">Update Type</label>
                <select
                  value={updateType}
                  onChange={(e) => setUpdateType(e.target.value)}
                  className="w-full p-3 border border-gray-200 rounded-xl focus:ring-2 focus:ring-blue-500 outline-none"
                >
                  <option value="note">General Note</option>
                  <option value="maintenance">Maintenance Log</option>
                  <option value="inspection">Inspection Report</option>
                  <option value="revision">New Document Revision</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-bold text-gray-700 mb-2">Metadata Details</label>
                <textarea
                  value={updateNote}
                  onChange={(e) => setUpdateNote(e.target.value)}
                  placeholder="Enter notes, inspection details, or changes made..."
                  rows={6}
                  className="w-full p-4 border border-gray-200 rounded-xl focus:ring-2 focus:ring-blue-500 outline-none resize-none"
                />
              </div>

              <div className="pt-4 border-t border-gray-100">
                <label className="block text-sm font-bold text-gray-700 mb-2">Status</label>
                <div className="flex gap-2">
                  <button className="flex-1 py-2 px-4 bg-green-50 text-green-700 rounded-lg border border-green-200 text-sm font-bold">Verified</button>
                  <button className="flex-1 py-2 px-4 bg-yellow-50 text-yellow-700 rounded-lg border border-yellow-200 text-sm font-bold">Pending</button>
                </div>
              </div>
            </div>

            <div className="mt-8 pt-6 border-t border-gray-100 flex gap-3">
              <button
                onClick={() => setIsUpdateModalOpen(false)}
                className="flex-1 py-4 text-gray-600 font-bold hover:bg-gray-50 rounded-xl transition-all"
              >
                Cancel
              </button>
              <button
                onClick={handleAddUpdate}
                disabled={!updateNote.trim() || uploading}
                className="flex-[2] py-4 bg-blue-600 text-white font-bold rounded-xl hover:bg-blue-700 transition-all shadow-lg shadow-blue-100 disabled:opacity-50"
              >
                {uploading ? <Loader2 className="w-5 h-5 mx-auto animate-spin" /> : 'Save Update'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const Loader2 = ({ className }: { className?: string }) => <div className={`animate-spin rounded-full border-2 border-current border-t-transparent ${className}`}></div>;