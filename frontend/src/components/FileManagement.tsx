import { useState, useEffect } from 'react';
import { FileText, Image as ImageIcon, Folder, Search, Calendar, User, Download, Eye, Plus, ChevronRight, ChevronDown, Home, Zap, Trash2, X, CheckCircle, AlertCircle, Paperclip } from 'lucide-react';
import { uploadFile, uploadFileAtPath, listFiles, createFileRecord, deleteFileRecord, deleteFile, type DocumentUpdate } from '../lib/supabase';
import { API_URL } from '../lib/api';
import { useAuth } from '../modules/auth/AuthContext';


const Toast = ({ message, type, onClose }: { message: string; type: 'success' | 'error'; onClose: () => void }) => {
  useEffect(() => {
    const timer = setTimeout(onClose, 15000); // Auto-dismiss after 15 seconds
    return () => clearTimeout(timer);
  }, [onClose]);

  return (
    <div className={`fixed bottom-8 right-8 px-6 py-4 rounded-lg shadow-lg flex items-start gap-3 animate-slide-up z-50 max-w-md ${type === 'success' ? 'bg-green-50 border border-green-200 text-green-700' : 'bg-red-50 border border-red-200 text-red-700'
      }`}>
      {type === 'success' ? (
        <CheckCircle className="w-5 h-5 mt-0.5 text-green-500" />
      ) : (
        <AlertCircle className="w-5 h-5 mt-0.5 text-red-500" />
      )}
      <div className="flex-1">
        <h4 className="font-medium text-sm mb-1">{type === 'success' ? 'Success' : 'Error'}</h4>
        <p className="text-sm opacity-90">{message}</p>
      </div>
      <button onClick={onClose} className="p-1 hover:bg-black/5 rounded transition-colors">
        <X className="w-4 h-4" />
      </button>
    </div>
  );
};

const subTypes = ['Drawing', 'Reports', 'Specs'];

const defaultCategories = [
  { name: 'Architectural', count: 0, icon: Home },
  { name: 'Mechanical', count: 0, icon: Zap },
  { name: 'Electrical', count: 0, icon: Zap },
  { name: 'Plumbing', count: 0, icon: Zap },
  { name: 'Structural', count: 0, icon: Home },
  { name: 'Civil', count: 0, icon: Home },
  { name: 'Waterproofing', count: 0, icon: Zap },
];

export function FileManagement() {
  const { profile } = useAuth();
  const [expandedBuildings, setExpandedBuildings] = useState<string[]>([]);
  const [expandedCategories, setExpandedCategories] = useState<string[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<{ buildingId: string; buildingName: string; type: string; subType: string } | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | number | null>(null);
  const [fetchedFiles, setFetchedFiles] = useState<any[]>([]);
  const [uploading, setUploading] = useState(false);
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [toast, setToast] = useState<{ show: boolean; message: string; type: 'success' | 'error' }>({
    show: false,
    message: '',
    type: 'success'
  });

  const [buildings, setBuildings] = useState<any[]>([]);

  // Update Modal State
  const [showUpdateModal, setShowUpdateModal] = useState(false);
  const [updateType, setUpdateType] = useState<DocumentUpdate['type']>('note');
  const [updateTitle, setUpdateTitle] = useState('');
  const [updateNote, setUpdateNote] = useState('');
  const [updateFile, setUpdateFile] = useState<File | null>(null);
  const [updateAttachments, setUpdateAttachments] = useState<File[]>([]);
  const [submittingUpdate, setSubmittingUpdate] = useState(false);

  // File Updates State
  const [fileUpdates, setFileUpdates] = useState<any[]>([]);

  // Load updates when selected file changes
  useEffect(() => {
    if (selectedFile) {
      fetch(`${API_URL}/_api/files/updates/${selectedFile}`)
        .then(res => res.json())
        .then(data => setFileUpdates(data || []))
        .catch(err => console.error('Error fetching updates:', err));
    } else {
      setFileUpdates([]);
    }
  }, [selectedFile]);

  // Load data function extracted for re-use
  const refreshData = async () => {
    try {
      // 1. Fetch Buildings (filtered by company)
      const companyParam = profile?.company ? `?companyId=${encodeURIComponent(profile.company)}` : '';
      const response = await fetch(`${API_URL}/_api/buildings${companyParam}`);
      if (!response.ok) throw new Error('Failed to fetch buildings');
      const buildingsData = await response.json();

      // 2. Count files from storage for each building/category/subType
      const mappedBuildings = await Promise.all(buildingsData.map(async (b: any) => {
        let buildingTotal = 0;
        const categoriesWithCounts = await Promise.all(defaultCategories.map(async (cat) => {
          let catTotal = 0;
          const subTypeCounts: Record<string, number> = {};

          for (const sub of subTypes) {
            try {
              const files = await listFiles('test-building-files', `${b.name}/${cat.name}/${sub}`);
              const fileCount = files.filter((f: any) => f.id).length;
              subTypeCounts[sub] = fileCount;
              catTotal += fileCount;
            } catch {
              subTypeCounts[sub] = 0;
            }
          }

          buildingTotal += catTotal;
          return { ...cat, count: catTotal, subTypeCounts };
        }));

        return {
          id: b.id,
          name: b.name,
          fileCount: buildingTotal,
          categories: categoriesWithCounts
        };
      }));

      setBuildings(mappedBuildings);
      sessionStorage.setItem('fm_buildings', JSON.stringify(mappedBuildings));

      setSelectedCategory(prev => {
        if (prev && !mappedBuildings.some((b: any) => b.id === prev.buildingId)) return null;
        return prev;
      });
      setExpandedBuildings(prev => prev.filter(id => mappedBuildings.some((b: any) => b.id === id)));
    } catch (error) {
      console.error('Error refreshing data:', error);
    }
  };

  // Load buildings and then counts (wait for profile to load)
  useEffect(() => {
    if (!profile?.company) return;
    refreshData();
    const interval = setInterval(refreshData, 15000);
    return () => clearInterval(interval);
  }, [profile?.company]);

  const getFolderPath = () => {
    if (!selectedCategory) return '';
    return `${selectedCategory.buildingName}/${selectedCategory.type}/${selectedCategory.subType}`;
  };

  // Load files from Supabase when category changes
  useEffect(() => {
    if (selectedCategory) {
      loadCategoryFiles();
    } else {
      setFetchedFiles([]);
    }
  }, [selectedCategory]);

  const loadCategoryFiles = async () => {
    const folderPath = getFolderPath();
    if (!folderPath || !selectedCategory) return;

    try {
      setLoadingFiles(true);
      // Fetch exact path files from S3
      const files = await listFiles('test-building-files', folderPath);
      // AND Fetch legacy files directly in the base category path from S3 (e.g. Architectural/ instead of Architectural/Drawing)
      let legacyFiles: any[] = [];
      if (selectedCategory.subType === 'Drawing') {
        legacyFiles = await listFiles('test-building-files', `${selectedCategory.buildingName}/${selectedCategory.type}`);
        // Filter out any folders that happen to be named after subtypes
        legacyFiles = legacyFiles.filter((f: any) => f.id && !subTypes.includes(f.name));
      }

      const allFiles = [...files, ...legacyFiles];

      // Transform Supabase files to UI format
      // Filter out items without ID (folders) to prevent "corrupted" downloads of folder placeholders
      const formattedFiles = (allFiles || [])
        .filter(f => f.id)
        .map(f => ({
          id: f.id,
          name: f.name.replace(/^\d+_/, ''), // Remove timestamp prefix if present for display
          _realName: f.name, // Keep real name for deletion/download
          folderPath: folderPath, // Store path for reliable download context
          mimeType: f.metadata?.mimetype, // Store mime type for proper download handling
          format: f.name.split('.').pop()?.toUpperCase() || 'FILE',
          uploadedBy: profile?.name || profile?.email || 'User',
          uploadDate: new Date(f.created_at).toLocaleDateString(),
          size: (f.metadata?.size ? (f.metadata.size / 1024 / 1024).toFixed(1) + ' MB' : '0 MB'),
          linkedTo: null,
          tags: [],
          recentUpdate: null,
          isSupabase: true,
        }));

      // Fetch the most recent update for each file to populate the green banner
      const filesWithUpdates = await Promise.all(formattedFiles.map(async (file: any) => {
        try {
          const res = await fetch(`${API_URL}/_api/files/updates/${file.id}`);
          const updates = await res.json();
          if (updates && updates.length > 0) {
            const latest = updates[0];
            return {
              ...file,
              recentUpdate: {
                type: latest.metadata?.title || (latest.type === 'new_file_upload' ? 'Version Update' : 'Note'),
                description: latest.metadata?.note || '',
                by: latest.metadata?.uploader_name || profile?.name || 'User',
                date: new Date(latest.created_at).toLocaleDateString(),
                photos: (latest.metadata?.attachments || []).length,
                attachments: latest.metadata?.attachments || [],
              }
            };
          }
        } catch (e) { /* ignore */ }
        return file;
      }));

      setFetchedFiles(filesWithUpdates);
    } catch (error) {
      console.error('Error loading files:', error);
    } finally {
      setLoadingFiles(false);
    }
  };

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file || !selectedCategory) return;

    try {
      setUploading(true);
      const folderPath = getFolderPath();

      // Optimistic Add
      const safeName = file.name.replace(/[#?+]/g, '_');
      const tempId = `temp-${Date.now()}`;
      const tempFile: any = {
        id: tempId,
        name: safeName,
        _realName: safeName,
        format: safeName.split('.').pop()?.toUpperCase() || 'FILE',
        size: (file.size / 1024 / 1024).toFixed(1) + ' MB',
        uploadedBy: profile?.name || profile?.email || 'User',
        uploadDate: new Date().toLocaleDateString(),
        linkedTo: null,
        tags: [],
        recentUpdate: null,
        mimeType: file.type,
        folderPath: folderPath,
        isSupabase: true,
        // isOptimistic: true // Could use this for opacity styling if desired
      };
      setFetchedFiles(prev => [...prev, tempFile].sort((a: any, b: any) => a.name.localeCompare(b.name)));

      const safeFile = new File([file], safeName, { type: file.type });
      const { data: result, error: uploadError } = await uploadFile(safeFile, 'test-building-files', folderPath);

      if (uploadError) {
        setFetchedFiles(prev => prev.filter((f: any) => f.id !== tempId));
        console.error('Storage upload error:', uploadError);
        setToast({
          show: true,
          message: `Upload failed: ${uploadError?.message || 'Unknown storage error'}`,
          type: 'error'
        });
        return;
      }

      if (result) {
        console.log('File uploaded successfully:', result);

        // Create DB Record
        const { error: dbError } = await createFileRecord({
          companyId: profile?.company || 'default_company',
          buildingId: selectedCategory.buildingId,
          folder: selectedCategory.type.toLowerCase(), // Ensure enum compatibility
          filename: file.name,
          fileType: file.name.split('.').pop() || 'unknown',
          s3Key: result.fileName,
        });

        if (dbError) {
          console.warn('Database metadata create error (non-critical):', dbError);
        }

        setToast({
          show: true,
          message: 'File uploaded successfully',
          type: 'success'
        });

        // Reload files and counts
        await loadCategoryFiles();
        await refreshData();

        // Auto-sync documents for AI (if it's a PDF)
        if (file.name.toLowerCase().endsWith('.pdf')) {
          syncBuildingDocuments(selectedCategory.buildingName);
        }

        // Reset file input
        event.target.value = '';
      }
    } catch (error) {
      console.error('Error uploading file:', error);
      setToast({
        show: true,
        message: 'An unexpected error occurred during upload',
        type: 'error'
      });
    } finally {
      setUploading(false);
    }
  };

  const handleDeleteFile = async (file: any) => {
    try {
      if (!selectedCategory) return;

      const folderPath = getFolderPath();
      const confirmed = true; // Auto-confirm to fix UI lag/blocking
      if (!confirmed) return;

      console.log('Attempting verify delete for:', file.name, 'Real Name:', file._realName, 'Folder:', folderPath);

      // Optimistic update: Remove file from UI immediately
      const previousFiles = [...fetchedFiles];
      setFetchedFiles(prev => prev.filter(f => f.id !== file.id));

      try {
        // Construct full path
        // Note: If listFiles matches how we stored it, file._realName is just the filename part.
        const fullPath = folderPath ? `${folderPath}/${file._realName}` : file._realName;

        // Permanent delete from storage
        const deleteSuccess = await deleteFile(fullPath, 'test-building-files');

        if (!deleteSuccess) {
          throw new Error("Storage delete failed");
        }

        // Delete from Database
        const dbSuccess = await deleteFileRecord(fullPath, file.name);

        if (dbSuccess) {
          setToast({
            show: true,
            message: 'File permanently deleted',
            type: 'success'
          });
          refreshData(); // Background refresh
        } else {
          throw new Error("Database delete failed");
        }
      } catch (error) {
        // Revert on failure
        console.error('Error deleting file:', error);
        setFetchedFiles(previousFiles);
        setToast({
          show: true,
          message: 'Failed to delete file',
          type: 'error'
        });
      }
    } catch (error) {
      console.error('Error deleting file:', error);
      setToast({
        show: true,
        message: 'An unexpected error occurred during deletion',
        type: 'error'
      });
    }
  };

  /*
  const buildings = [
    // ... replaced by state
  ];
  */

  const toggleBuilding = (buildingId: string) => {
    setExpandedBuildings(prev =>
      prev.includes(buildingId)
        ? prev.filter(id => id !== buildingId)
        : [...prev, buildingId]
    );
  };

  const toggleCategory = (categoryKey: string) => {
    setExpandedCategories(prev =>
      prev.includes(categoryKey)
        ? prev.filter(k => k !== categoryKey)
        : [...prev, categoryKey]
    );
  };

  const selectCategory = (buildingName: string, buildingId: string, type: string, subType: string) => {
    setSelectedCategory({ buildingName, buildingId, type, subType });
    setSelectedFile(null);
  };

  // No static files anymore
  const currentFiles = fetchedFiles;

  const selectedFileData = currentFiles.find(f => f.id === selectedFile);

  const getFileIcon = (format: string) => {
    const fmt = format.toUpperCase();
    if (fmt === 'PDF') return <FileText className="w-5 h-5 text-red-500" />;
    if (fmt === 'DWG') return <ImageIcon className="w-5 h-5 text-blue-500" />;
    if (['JPG', 'PNG', 'JPEG'].includes(fmt)) return <ImageIcon className="w-5 h-5 text-purple-500" />;
    return <FileText className="w-5 h-5 text-gray-500" />;
  };

  // Calculate total files from dynamic stats
  const totalFiles = buildings.reduce((acc, b) => acc + b.fileCount, 0);

  // Hidden file input ref (simulated by ID)
  const triggerFileUpload = () => {
    document.getElementById('hidden-file-input')?.click();
  };

  // Get Supabase public URL for a file
  const getFileUrl = (file: any) => {
    const folderPath = file.folderPath || getFolderPath();
    if (!folderPath) return '';

    // Encode each path segment separately to preserve slashes
    const encodedPath = folderPath.split('/').map((segment: string) => encodeURIComponent(segment)).join('/');
    const encodedFileName = encodeURIComponent(file._realName || file.name);

    // Construct Supabase storage public URL
    const supabaseUrl = import.meta.env.VITE_SUPABASE_URL || 'https://lxlrwiltjwfbvjkhsgis.supabase.co';
    return `${supabaseUrl}/storage/v1/object/public/test-building-files/${encodedPath}/${encodedFileName}`;
  };

  // View file in new tab
  const handleViewFile = (file: any) => {
    const url = getFileUrl(file);
    window.open(url, '_blank');
  };

  // Download file - use server-side forced download
  const handleDownloadFile = (file: any) => {
    try {
      let url = getFileUrl(file);
      if (!url) throw new Error('Could not generate download URL');

      // Append ?download query param to force Supabase to send "Content-Disposition: attachment"
      // This tells the browser to SAVE the file to Downloads/Finder instead of opening it
      // We pass the clean filename so it saves with the correct name
      // Note: we use 'download' param which public buckets support to force attachment
      const cleanName = file.name;
      // Check if URL already has params (it shouldn't for public URL, but good practice)
      const separator = url.includes('?') ? '&' : '?';
      // Append download=filename
      url += `${separator}download=${encodeURIComponent(cleanName)}`;

      console.log('Triggering native download via:', url);

      // Create standard anchor tag and click it
      // This relies on the browser's native download manager
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', cleanName); // Helper hint
      link.target = '_self'; // Ensure it doesn't open new tab if possible, though download header handles it
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      setToast({ show: true, message: 'Download started', type: 'success' });
    } catch (error) {
      console.error('Download error:', error);
      setToast({ show: true, message: 'Failed to download file', type: 'error' });
    }
  };

  // Handle creating an update
  const handleCreateUpdate = async () => {
    if (!selectedFileData || !profile?.id) {
      if (!profile?.id) console.error("No user profile found");
      return;
    }

    try {
      setSubmittingUpdate(true);

      // --- VERSION UPDATE ---
      if (updateType === 'new_file_upload') {
        if (!updateFile) {
          setToast({ show: true, message: 'Please select a file to upload', type: 'error' });
          return;
        }

        const folderPath = getFolderPath();
        if (!folderPath) return;

        const oldRealName = selectedFileData._realName;
        // Compute _v2, _v3 based on old filename
        const nameParts = oldRealName.split('.');
        const ext = nameParts.length > 1 ? nameParts.pop() : '';
        const baseNameRaw = nameParts.join('.');

        const match = baseNameRaw.match(/_v(\d+)$/i);
        let nextVersion = 2;
        let baseName = baseNameRaw;
        if (match) {
          nextVersion = parseInt(match[1], 10) + 1;
          baseName = baseNameRaw.replace(/_v\d+$/i, '');
        }

        const newFilename = ext ? `${baseName}_v${nextVersion}.${ext}` : `${baseName}_v${nextVersion}`;
        const oldPath = `${folderPath}/${oldRealName}`;

        const formData = new FormData();
        // create a new File instance so the server sees the correct filename
        const safeUpdateFile = new File([updateFile], newFilename, { type: updateFile.type });
        formData.append('file', safeUpdateFile);

        const replaceParams = new URLSearchParams({
          old_path: oldPath,
          folder_path: folderPath,
          bucket: 'test-building-files',
          db_file_id: String(selectedFileData.id),
        });

        // Use backend /replace endpoint
        const replaceRes = await fetch(`${API_URL}/_api/storage/replace?${replaceParams}`, {
          method: 'POST',
          body: formData
        });

        if (!replaceRes.ok) {
          throw new Error('File replacement failed');
        }

        const replaceData = await replaceRes.json();
        const activeDocId = replaceData.new_s3_id || String(selectedFileData.id);

        const updatePayload = {
          document_id: activeDocId,
          user_id: String(profile.id),
          type: 'new_file_upload',
          metadata: {
            title: updateTitle.trim() || 'Version Update',
            note: updateNote,
            uploader_name: profile.name || profile.email || 'You',
            replaced_file: oldRealName,
            previous_version_path: replaceData.trash_path,
            new_filename: newFilename,
            size: updateFile.size,
          }
        };

        const saveRes = await fetch(`${API_URL}/_api/files/update`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(updatePayload),
        });

        if (saveRes.ok) {
          const saved = await saveRes.json();
          setFileUpdates(prev => [saved, ...prev]);

          setFetchedFiles(prev => prev.map(f => f.id === selectedFileData.id ? {
            ...f,
            id: activeDocId, // Update to the new S3 ID so future notes append correctly
            name: newFilename, // Optimistically update name
            _realName: newFilename,
            recentUpdate: {
              type: saved.metadata?.title || 'Version Update',
              description: saved.metadata?.note || '',
              by: saved.metadata?.uploader_name || profile?.name || profile?.email || 'User',
              date: new Date(saved.created_at).toLocaleDateString(),
              photos: 0
            }
          } : f));
        }

        setToast({ show: true, message: 'File replaced successfully', type: 'success' });
        // After optimistic state update, update our tracking ID
        setSelectedFile(activeDocId);
        await refreshData();

        // --- NOTE / REGULAR UPDATE ---
      } else {
        if (!updateTitle.trim() && !updateNote.trim()) {
          setToast({ show: true, message: 'Please provide a title or note', type: 'error' });
          return;
        }

        // Upload actual attachments
        const attachmentPaths: string[] = [];
        for (const att of updateAttachments) {
          const safeAttName = att.name.replace(/[#?+]/g, '_');
          const exactPath = `note-attachments/${Date.now()}_${safeAttName}`;
          // Use uploadFileAtPath (from lib/supabase.ts) to push direct to s3 path
          const safeFile = new File([att], safeAttName, { type: att.type });
          const res = await uploadFileAtPath(safeFile, exactPath, 'test-building-files');
          if (!res.error) {
            attachmentPaths.push(exactPath);
          }
        }

        const updatePayload = {
          document_id: String(selectedFileData.id),
          user_id: String(profile.id),
          type: 'note',
          metadata: {
            title: updateTitle.trim() || 'Note',
            note: updateNote.trim(),
            uploader_name: profile.name || profile.email || 'User',
            attachments: attachmentPaths
          }
        };

        const saveRes = await fetch(`${API_URL}/_api/files/update`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(updatePayload),
        });

        if (!saveRes.ok) throw new Error('Failed to save update');

        const saved = await saveRes.json();
        setFileUpdates(prev => [saved, ...prev]);

        setFetchedFiles(prev => prev.map(f => f.id === selectedFileData.id ? {
          ...f,
          recentUpdate: {
            type: saved.metadata?.title || 'Note',
            description: saved.metadata?.note || '',
            by: saved.metadata?.uploader_name || profile?.name || profile?.email || 'User',
            date: new Date(saved.created_at).toLocaleDateString(),
            photos: (saved.metadata?.attachments || []).length
          }
        } : f));

        setToast({ show: true, message: 'Update added successfully', type: 'success' });
      }

      setShowUpdateModal(false);
      setUpdateNote('');
      setUpdateTitle('');
      setUpdateFile(null);
      setUpdateAttachments([]);
    } catch (error) {
      console.error('Update create error:', error);
      setToast({ show: true, message: 'Failed to add update', type: 'error' });
    } finally {
      setSubmittingUpdate(false);
    }
  };


  // Auto-sync documents for AI after file upload
  const syncBuildingDocuments = async (buildingName: string) => {
    try {
      await fetch(`${API_URL}/_api/ai/vectors/sync/${encodeURIComponent(buildingName)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      console.log(`Auto-synced documents for ${buildingName}`);
    } catch (error) {
      console.error('Auto-sync error:', error);
    }
  };

  return (
    <div className="p-8 max-w-7xl mx-auto relative">
      {/* Toast Notification */}
      {/* Toast Notification */}
      {toast.show && (
        <Toast
          message={toast.message}
          type={toast.type}
          onClose={() => setToast(prev => ({ ...prev, show: false }))}
        />
      )}

      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-gray-900 mb-2">File Management</h1>
          <p className="text-gray-600">
            {totalFiles} files organized by building and type
          </p>
        </div>
        <button
          onClick={triggerFileUpload}
          className="flex items-center gap-2 px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
        >
          <Plus className="w-5 h-5" />
          Upload File
        </button>
        <input
          id="hidden-file-input"
          type="file"
          onChange={handleFileUpload}
          disabled={uploading}
          className="hidden"
        />
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        <div className="bg-white rounded-xl p-4 border border-gray-200">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
              <Folder className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <div className="text-gray-900">{totalFiles}</div>
              <div className="text-sm text-gray-600">Total Files</div>
            </div>
          </div>
        </div>

        <div className="bg-white rounded-xl p-4 border border-gray-200">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
              <FileText className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <div className="text-gray-900">N/A</div>
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
              <div className="text-gray-900">{buildings.length}</div>
              <div className="text-sm text-gray-600">Buildings</div>
            </div>
          </div>
        </div>

        <div className="bg-gradient-to-br from-orange-50 to-red-50 rounded-xl p-4 border border-orange-200">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-orange-100 rounded-lg flex items-center justify-center">
              <Zap className="w-5 h-5 text-orange-600" />
            </div>
            <div>
              <div className="text-orange-900">N/A</div>
              <div className="text-sm text-orange-700">Upgrade Plans</div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Folder Tree Navigation */}
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <div className="flex items-center gap-2 mb-4">
            <Folder className="w-5 h-5 text-blue-600" />
            <h2 className="text-gray-900">Folders</h2>
          </div>

          <div className="space-y-1">
            {/* Buildings */}
            {buildings.map((building) => {
              const isExpanded = expandedBuildings.includes(building.id);
              return (
                <div key={building.id}>
                  <button
                    onClick={() => toggleBuilding(building.id)}
                    className="w-full flex items-center gap-2 px-3 py-2 hover:bg-gray-50 rounded-lg transition-colors"
                  >
                    {isExpanded ? (
                      <ChevronDown className="w-4 h-4 text-gray-600" />
                    ) : (
                      <ChevronRight className="w-4 h-4 text-gray-600" />
                    )}
                    <Folder className="w-4 h-4 text-blue-500" />
                    <span className="flex-1 text-left text-sm text-gray-900">{building.name}</span>
                    {building.fileCount > 0 && (
                      <span className="text-xs text-gray-500">{building.fileCount}</span>
                    )}
                  </button>

                  {isExpanded && (
                    <div className="ml-6 mt-1 space-y-1">
                      {building.categories.map((category: any, catIdx: number) => {
                        const categoryKey = `${building.id}-${category.name}`;
                        const isCatExpanded = expandedCategories.includes(categoryKey);
                        return (
                          <div key={category.name}>
                            <button
                              onClick={() => toggleCategory(categoryKey)}
                              className="w-full flex items-center gap-2 px-3 py-2 hover:bg-gray-50 rounded-lg transition-colors text-gray-700"
                            >
                              {isCatExpanded ? (
                                <ChevronDown className="w-4 h-4 text-gray-600" />
                              ) : (
                                <ChevronRight className="w-4 h-4 text-gray-600" />
                              )}
                              <Folder className="w-4 h-4 text-yellow-500" />
                              <span className="flex-1 text-left text-sm">{catIdx + 1}. {category.name}</span>
                              {category.count > 0 && (
                                <span className="text-xs text-gray-500">{category.count}</span>
                              )}
                            </button>

                            {isCatExpanded && (
                              <div className="ml-6 mt-1 space-y-1">
                                {subTypes.map((sub) => {
                                  const isSelected = selectedCategory?.buildingName === building.name && selectedCategory?.type === category.name && selectedCategory?.subType === sub;
                                  const subCount = category.subTypeCounts?.[sub] || 0;
                                  return (
                                    <button
                                      key={sub}
                                      onClick={() => selectCategory(building.name, building.id, category.name, sub)}
                                      className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg transition-colors ${isSelected ? 'bg-blue-50 text-blue-700' : 'hover:bg-gray-50 text-gray-700'
                                        }`}
                                    >
                                      <FileText className="w-4 h-4" />
                                      <span className="flex-1 text-left text-sm">{sub}</span>
                                      {subCount > 0 && (
                                        <span className="text-xs text-gray-500">{subCount}</span>
                                      )}
                                    </button>
                                  );
                                })}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}


          </div>
        </div>

        {/* Files List */}
        <div className="lg:col-span-2 space-y-4">
          {selectedCategory ? (
            <>
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h2 className="text-gray-900">{selectedCategory.buildingName}</h2>
                  <p className="text-sm text-gray-600">{selectedCategory.type} / {selectedCategory.subType} • {currentFiles.length} files</p>
                </div>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                  <input
                    type="text"
                    placeholder="Search..."
                    className="pl-9 pr-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                  />
                </div>
              </div>

              {loadingFiles && (
                <div className="text-center py-4">
                  <p className="text-gray-500 text-sm">Loading files...</p>
                </div>
              )}

              {!loadingFiles && currentFiles.length > 0 ? (
                currentFiles.map((file, idx) => (
                  <button
                    key={file.id || idx}
                    onClick={() => setSelectedFile(file.id)}
                    className={`w-full text-left bg-white rounded-xl p-6 border transition-all ${selectedFile === file.id
                      ? 'border-blue-500 ring-2 ring-blue-200 shadow-lg'
                      : 'border-gray-200 hover:border-gray-300'
                      }`}
                  >
                    <div className="flex items-start gap-4">
                      <div className="w-12 h-12 bg-gray-100 rounded-lg flex items-center justify-center flex-shrink-0">
                        {getFileIcon(file.format)}
                      </div>

                      <div className="flex-1 min-w-0">
                        <div className="flex items-start justify-between mb-2">
                          <div className="flex-1">
                            <h3 className="text-gray-900 mb-1">{file.name}</h3>
                            <div className="flex items-center gap-3 text-sm text-gray-600">
                              <span className="px-2 py-1 bg-gray-100 text-gray-700 rounded text-xs">
                                {file.format}
                              </span>
                              <span>{file.size}</span>
                              {file.isSupabase && (
                                <span className="px-2 py-1 bg-green-100 text-green-700 rounded text-xs">
                                  Synced
                                </span>
                              )}
                            </div>
                          </div>
                          <ChevronRight className={`w-5 h-5 transition-transform flex-shrink-0 ${selectedFile === file.id ? 'rotate-90 text-blue-600' : 'text-gray-400'
                            }`} />
                        </div>

                        {file.recentUpdate && (
                          <div className="p-3 bg-green-50 rounded-lg border border-green-200 mb-2">
                            <div className="flex items-start gap-2">
                              <div className="w-1.5 h-1.5 rounded-full bg-green-500 mt-2" />
                              <div className="flex-1">
                                <p className="text-sm text-green-900 font-medium">{file.recentUpdate.type}</p>
                                <p className="text-xs text-green-700 mt-1">{file.recentUpdate.description}</p>
                                {file.recentUpdate.attachments?.length > 0 && (
                                  <div className="mt-1 space-y-0.5">
                                    {file.recentUpdate.attachments.map((att: string, i: number) => (
                                      <a
                                        key={i}
                                        href={`${import.meta.env.VITE_SUPABASE_URL || 'https://lxlrwiltjwfbvjkhsgis.supabase.co'}/storage/v1/object/public/test-building-files/${att.split('/').map(c => encodeURIComponent(c)).join('/')}`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 hover:underline cursor-pointer"
                                        onClick={(e) => e.stopPropagation()}
                                      >
                                        <Paperclip className="w-3 h-3" />
                                        {att.split('/').pop()?.replace(/^\d+_/, '')}
                                      </a>
                                    ))}
                                  </div>
                                )}
                                <div className="flex items-center gap-3 text-xs text-green-600 mt-2">
                                  <div className="flex items-center gap-1">
                                    <User className="w-3 h-3" />
                                    {file.recentUpdate.by}
                                  </div>
                                  <div className="flex items-center gap-1">
                                    <Calendar className="w-3 h-3" />
                                    {file.recentUpdate.date}
                                  </div>
                                  {file.recentUpdate.photos > 0 && (
                                    <div className="flex items-center gap-1">
                                      <ImageIcon className="w-3 h-3" />
                                      {file.recentUpdate.photos} photos
                                    </div>
                                  )}
                                </div>
                              </div>
                            </div>
                          </div>
                        )}

                        <div className="flex items-center gap-2 text-xs text-gray-500">
                          <div className="flex items-center gap-1">
                            <User className="w-3 h-3" />
                            {file.uploadedBy}
                          </div>
                          <span>•</span>
                          <div className="flex items-center gap-1">
                            <Calendar className="w-3 h-3" />
                            {file.uploadDate}
                          </div>
                          {file.linkedTo && (
                            <>
                              <span>•</span>
                              <span className="text-blue-600">Linked: {file.linkedTo}</span>
                            </>
                          )}
                        </div>
                      </div>
                    </div>
                  </button>
                ))
              ) : (
                <div className="text-center py-12 bg-white rounded-xl border border-gray-200">
                  <Folder className="w-16 h-16 text-gray-400 mx-auto mb-4" />
                  <p className="text-gray-600">No files in this category yet</p>
                  <button
                    onClick={triggerFileUpload}
                    className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm"
                  >
                    {currentFiles.length > 0 ? 'Upload File' : 'Upload First File'}
                  </button>
                </div>
              )}
            </>
          ) : (
            <div className="text-center py-12 bg-white rounded-xl border border-gray-200">
              <Folder className="w-16 h-16 text-gray-400 mx-auto mb-4" />
              <p className="text-gray-600">Select a folder to view files</p>
            </div>
          )}
        </div>

        {/* File Details Sidebar */}
        <div className="space-y-6">
          {selectedFileData ? (
            <div className="bg-white rounded-xl border border-gray-200 p-6">
              <h2 className="text-gray-900 mb-4">File Details</h2>

              <div className="space-y-4 mb-6">
                <div>
                  <p className="text-sm text-gray-600 mb-1">Tags</p>
                  <div className="flex flex-wrap gap-2">
                    {selectedFileData.tags.map((tag: any, index: any) => (
                      <span key={index} className="px-2 py-1 bg-gray-100 text-gray-700 rounded text-xs">
                        {tag}
                      </span>
                    ))}
                    {selectedFileData.tags.length === 0 && <span className="text-xs text-gray-400">No tags</span>}
                  </div>
                </div>

                <div>
                  <p className="text-sm text-gray-600 mb-1">Format</p>
                  <p className="text-gray-900">{selectedFileData.format}</p>
                </div>

                <div>
                  <p className="text-sm text-gray-600 mb-1">Size</p>
                  <p className="text-gray-900">{selectedFileData.size}</p>
                </div>

                {selectedFileData.linkedTo && (
                  <div>
                    <p className="text-sm text-gray-600 mb-1">Linked To</p>
                    <p className="text-blue-600 hover:underline cursor-pointer">{selectedFileData.linkedTo}</p>
                  </div>
                )}
              </div>

              <div className="space-y-2">
                <button
                  onClick={() => handleViewFile(selectedFileData)}
                  className="w-full py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors flex items-center justify-center gap-2"
                >
                  <Eye className="w-4 h-4" />
                  View File
                </button>
                <button
                  onClick={() => handleDownloadFile(selectedFileData)}
                  className="w-full py-2 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors flex items-center justify-center gap-2"
                >
                  <Download className="w-4 h-4" />
                  Download
                </button>
                <button
                  onClick={() => setShowUpdateModal(true)}
                  className="w-full py-2 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors flex items-center justify-center gap-2"
                >
                  <Plus className="w-4 h-4" />
                  Add Update
                </button>
                {selectedFileData.isSupabase && (
                  <button
                    onClick={() => handleDeleteFile(selectedFileData)}
                    className="w-full py-2 border border-red-200 text-red-600 rounded-lg hover:bg-red-50 transition-colors flex items-center justify-center gap-2"
                  >
                    <Trash2 className="w-4 h-4" />
                    Delete File
                  </button>
                )}
              </div>

              {/* UPDATES SCROLLABLE LIST */}
              <div className="mt-8 pt-6 border-t border-gray-100">
                <h3 className="text-gray-900 font-semibold mb-4">Updates & Notes</h3>
                <div className="space-y-4 max-h-[400px] overflow-y-auto pr-2 custom-scrollbar">
                  {fileUpdates.length > 0 ? fileUpdates.map((update, idx) => (
                    <div key={update.id || idx} className="bg-gray-50 border border-gray-100 rounded-lg p-4">
                      <div className="flex justify-between items-start mb-2">
                        <div>
                          <h4 className="text-sm font-semibold text-gray-900">{update.metadata?.title || 'Note'}</h4>
                          <span className="text-xs text-blue-600 bg-blue-50 px-2 py-0.5 rounded-full mt-1 inline-block">
                            {update.type === 'new_file_upload' ? 'Version Update' : 'Note'}
                          </span>
                        </div>
                        <button
                          onClick={async () => {
                            if (!window.confirm("Delete this update?")) return;
                            try {
                              await fetch(`${API_URL}/_api/files/updates/${update.id}`, { method: 'DELETE' });
                              setFileUpdates(prev => prev.filter(u => u.id !== update.id));
                            } catch (e) { console.error('Error deleting:', e); }
                          }}
                          className="text-gray-400 hover:text-red-500"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                      <p className="text-sm text-gray-700 whitespace-pre-wrap my-2">{update.metadata?.note}</p>

                      {update.type === 'new_file_upload' && update.metadata?.previous_version_path && (
                        <div className="my-3">
                          <a
                            href={`${import.meta.env.VITE_SUPABASE_URL || 'https://lxlrwiltjwfbvjkhsgis.supabase.co'}/storage/v1/object/public/test-building-files/${update.metadata.previous_version_path.split('/').map((c: string) => encodeURIComponent(c)).join('/')}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-md text-xs font-medium transition-colors"
                          >
                            <Download className="w-3.5 h-3.5" />
                            View Previous Version ({update.metadata.replaced_file?.split('/').pop()?.replace(/^\d+_/, '') || 'File'})
                          </a>
                        </div>
                      )}

                      {update.metadata?.attachments?.length > 0 && (
                        <div className="my-3 space-y-1">
                          {update.metadata.attachments.map((att: string, i: number) => (
                            <a
                              key={i}
                              href={`${import.meta.env.VITE_SUPABASE_URL || 'https://lxlrwiltjwfbvjkhsgis.supabase.co'}/storage/v1/object/public/test-building-files/${att.split('/').map(c => encodeURIComponent(c)).join('/')}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="flex items-center gap-2 text-xs text-blue-600 hover:text-blue-800 hover:underline cursor-pointer"
                            >
                              <Paperclip className="w-3 h-3" />
                              <span>{att.split('/').pop()?.replace(/^\d+_/, '')}</span>
                            </a>
                          ))}
                        </div>
                      )}

                      <div className="flex items-center gap-4 text-xs text-gray-500 mt-3 pt-3 border-t border-gray-200">
                        <div className="flex items-center gap-1">
                          <User className="w-3 h-3" />
                          <span className="font-medium text-gray-700">{update.metadata?.uploader_name || profile?.name || profile?.email || 'User'}</span>
                        </div>
                        <div className="flex items-center gap-1">
                          <Calendar className="w-3 h-3" />
                          {new Date(update.created_at).toLocaleDateString()} at {new Date(update.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                        </div>
                      </div>
                    </div>
                  )) : (
                    <p className="text-sm text-gray-500 text-center py-4 italic">No updates or notes yet.</p>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <div className="bg-white rounded-xl border border-gray-200 p-6 text-center">
              <FileText className="w-16 h-16 text-gray-400 mx-auto mb-4" />
              <p className="text-gray-600">Select a file to view details</p>
            </div>
          )}
        </div>
        {/* Update Modal */}
        {showUpdateModal && (
          <div className="fixed inset-0 bg-black/50 z-[100] flex items-center justify-center p-4">
            <div className="bg-white rounded-xl max-w-md w-full p-6 shadow-xl animate-scale-up">
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-lg font-semibold">
                  Add Update: {selectedFileData?._realName || 'File'}
                </h3>
                <button
                  onClick={() => setShowUpdateModal(false)}
                  className="p-1 hover:bg-gray-100 rounded-full text-gray-500 transition-colors"
                  title="Close"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Update Type</label>
                  <select
                    value={updateType}
                    onChange={(e) => {
                      setUpdateType(e.target.value as any);
                      setUpdateTitle('');
                      setUpdateNote('');
                    }}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none bg-white"
                  >
                    <option value="note">Note</option>
                    <option value="new_file_upload">Version Update</option>
                  </select>
                </div>

                {updateType !== 'new_file_upload' && (
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Title</label>
                    <input
                      type="text"
                      value={updateTitle}
                      onChange={(e) => setUpdateTitle(e.target.value)}
                      placeholder="Enter update title..."
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-sm"
                    />
                  </div>
                )}

                {updateType === 'new_file_upload' && (
                  <div className="p-3 bg-blue-50 rounded-lg border border-blue-100">
                    <label className="block text-sm font-medium text-blue-900 mb-2">Select Replacement File</label>
                    <input
                      type="file"
                      onChange={(e) => setUpdateFile(e.target.files?.[0] || null)}
                      className="w-full text-sm text-gray-600 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-blue-600 file:text-white hover:file:bg-blue-700 cursor-pointer"
                    />
                    {updateFile && (
                      <p className="text-xs text-green-700 mt-2 flex items-center gap-1 font-medium">
                        <CheckCircle className="w-3 h-3" /> Selected: {updateFile.name}
                      </p>
                    )}
                  </div>
                )}

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    {updateType === 'new_file_upload' ? 'Version Notes' : 'Content'}
                  </label>
                  <textarea
                    value={updateNote}
                    onChange={(e) => setUpdateNote(e.target.value)}
                    placeholder="Type your notes here..."
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg h-32 resize-none focus:ring-2 focus:ring-blue-500 outline-none text-sm"
                  />

                  {updateType === 'note' && (
                    <div className="mt-3">
                      <label className="block text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wider">Attachments (Optional)</label>
                      <input
                        type="file"
                        multiple
                        onChange={(e) => setUpdateAttachments(Array.from(e.target.files || []))}
                        className="w-full text-xs text-gray-500 file:mr-3 file:py-1 file:px-3 file:rounded-md file:border-0 file:text-xs file:font-medium file:bg-gray-100 file:text-gray-700 hover:file:bg-gray-200 cursor-pointer"
                      />
                      {updateAttachments.length > 0 && (
                        <div className="mt-2 space-y-1">
                          {updateAttachments.map((f, i) => (
                            <p key={i} className="text-xs text-gray-600 flex items-center gap-1">
                              <Paperclip className="w-3 h-3" /> {f.name}
                            </p>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>

                <div className="flex gap-3 pt-2">
                  <button
                    onClick={() => setShowUpdateModal(false)}
                    className="flex-1 py-2 px-4 border border-gray-300 rounded-lg hover:bg-gray-50 text-gray-700 font-medium transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleCreateUpdate}
                    disabled={submittingUpdate || (updateType === 'new_file_upload' && !updateFile)}
                    className="flex-1 py-2 px-4 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 transition-colors"
                  >
                    {submittingUpdate ? (
                      <>
                        <div className="w-4 h-4 border-2 border-white/20 border-t-white rounded-full animate-spin" />
                        Saving...
                      </>
                    ) : 'Save Update'}
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