import { createClient } from '@supabase/supabase-js';
import { API_URL } from './api';

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL || '';
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY || '';

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

// ================================================================
// STORAGE HELPERS — All WRITES go through the backend (service role)
// so RLS never blocks us. Reads use direct Supabase public URLs.
// ================================================================

/**
 * Upload a file to storage via the backend (service-role bypass).
 * Also creates the File DB record in one call.
 */
export async function uploadFile(
  file: File,
  bucket: string = 'test-building-files',
  folder: string = '',
  meta?: { buildingId?: string; companyId?: string; uploadedBy?: string }
) {
  const fileName = folder ? `${folder}/${Date.now()}_${file.name}` : `${Date.now()}_${file.name}`;

  const form = new FormData();
  form.append('file', file);

  const params = new URLSearchParams({
    path: fileName,
    bucket,
    ...(meta?.buildingId ? { building_id: meta.buildingId } : {}),
    ...(meta?.companyId ? { company_id: meta.companyId } : {}),
    ...(meta?.uploadedBy ? { uploaded_by: meta.uploadedBy } : {}),
  });

  const res = await fetch(`${API_URL}/_api/storage/upload?${params}`, {
    method: 'POST',
    body: form,
  });

  if (!res.ok) {
    const errText = await res.text();
    console.error('Upload error via backend:', errText);
    return { data: null, error: new Error(errText) };
  }

  const json = await res.json();
  return {
    data: {
      fileName: json.path,
      publicUrl: json.publicUrl,
      dbId: json.db_id,
      uploadedAt: new Date(),
    },
    error: null,
  };
}

/**
 * Upload a file to a specific exact path via the backend.
 * Used for attachments where we control the full path.
 */
export async function uploadFileAtPath(
  file: File,
  exactPath: string,
  bucket: string = 'test-building-files'
) {
  const form = new FormData();
  form.append('file', file);

  const params = new URLSearchParams({ path: exactPath, bucket });
  const res = await fetch(`${API_URL}/_api/storage/upload?${params}`, {
    method: 'POST',
    body: form,
  });

  if (!res.ok) {
    const errText = await res.text();
    console.error('Upload-at-path error via backend:', errText);
    return { data: null, error: new Error(errText) };
  }

  const json = await res.json();
  return { data: { fileName: json.path, publicUrl: json.publicUrl }, error: null };
}

/** List files in a bucket folder — read is fine with anon key on public bucket */
export async function listFiles(bucket: string = 'test-building-files', folder: string = '') {
  const { data, error } = await supabase.storage
    .from(bucket)
    .list(folder, { limit: 100, offset: 0, sortBy: { column: 'name', order: 'asc' } });

  if (error) {
    console.error('List error:', error);
    return [];
  }

  // Hide system folders from UI
  return data.filter((item: any) =>
    item.name !== 'recently-deleted' &&
    item.name !== 'trash' &&
    !item.name.startsWith('.')
  );
}

/** Delete a file via backend (service-role, RLS-safe) */
export async function deleteFile(fileName: string, bucket: string = 'test-building-files') {
  try {
    const response = await fetch(
      `${API_URL}/_api/storage/file?path=${encodeURIComponent(fileName)}&bucket=${bucket}`,
      { method: 'DELETE' }
    );
    if (!response.ok) {
      console.error('Delete error via backend:', await response.text());
      return false;
    }
    return true;
  } catch (error) {
    console.error('Delete error via backend:', error);
    return false;
  }
}

/** Move a file via backend (service-role, RLS-safe) */
export async function moveFile(fromPath: string, toPath: string, bucket: string = 'test-building-files') {
  try {
    const res = await fetch(`${API_URL}/_api/storage/move`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from_path: fromPath, to_path: toPath, bucket }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

/** Download a file — reads from public URL directly (no auth needed) */
export async function downloadFile(fileName: string, bucket: string = 'test-building-files') {
  const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL || '';
  const url = `${SUPABASE_URL}/storage/v1/object/public/${bucket}/${fileName}`;
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    return await res.blob();
  } catch {
    return null;
  }
}

// ===== BUILDINGS TABLE OPERATIONS =====

// Get all buildings
export async function getBuildings() {
  const { data, error } = await supabase
    .from('Building')
    .select('*')
    .order('created_at', { ascending: false });

  if (error) {
    console.error('Error fetching buildings:', error);
    return [];
  }

  return data;
}

// Create a building
export async function createBuilding(building: { name: string; address: string; floors: string; sqft: string }) {
  const { data, error } = await supabase
    .from('Building')
    .insert([building])
    .select()
    .single();

  if (error) {
    console.error('Error creating building:', error);
    return null;
  }

  return data;
}

// Delete a building
export async function deleteBuilding(id: string) {
  const { error } = await supabase
    .from('Building')
    .delete()
    .eq('id', id);

  if (error) {
    console.error('Error deleting building:', error);
    return false;
  }

  return true;
}

// Update a building
export async function updateBuilding(id: string, updates: Partial<{ name: string; address: string; floors: string; sqft: string }>) {
  const { data, error } = await supabase
    .from('Building')
    .update(updates)
    .eq('id', id)
    .select()
    .single();

  if (error) {
    console.error('Error updating building:', error);
    return null;
  }

  return data;
}

// ===== FILES TABLE OPERATIONS =====

export async function createFileRecord(fileData: {
  companyId: string;
  buildingId: string;
  folder: string;
  filename: string;
  fileType: string;
  s3Key: string;
}) {
  /* 
   * Retry logic for FolderType enum constraints.
   * Supabase/Postgres enums are strict. If the provided folder name doesn't match,
   * we try standard fallbacks to ensure the file record is still created.
   */
  let attemptError = null;
  const folderTitle = fileData.folder.charAt(0).toUpperCase() + fileData.folder.slice(1).toLowerCase();
  const fallbacks = [
    fileData.folder.toUpperCase(),
    folderTitle,
    fileData.folder,
    fileData.folder.toLowerCase(),
    'OTHER',
    'Other',
    'other'
  ];
  const uniqueFallbacks = [...new Set(fallbacks)];

  for (const folderValue of uniqueFallbacks) {
    const payload = {
      id: crypto.randomUUID(), // Ensure ID is provided since DB constraint failed earlier
      ...fileData,
      folder: folderValue
    };

    const { data, error } = await supabase
      .from('File')
      .insert([payload])
      .select()
      .single();

    if (!error) {
      return { data, error: null };
    }

    // specific check for enum violation or similar data errors
    if (error.code === '22P02' || error.message?.includes('enum')) {
      attemptError = error;
      continue; // Try next fallback
    } else {
      // Other error (auth, connection, etc) - fail info immediately
      console.error('Error creating file record:', error);
      return { data: null, error };
    }
  }

  // If all fallbacks failed
  console.error('All folder type attempts failed. Last error:', attemptError);
  return { data: null, error: attemptError };
}

// Delete file record from DB
// Delete file record from DB - using backend proxy to bypass RLS
export async function deleteFileRecord(s3Key: string, filename?: string) {
  try {
    let url = `${API_URL}/_api/storage/record?s3Key=${encodeURIComponent(s3Key)}`;
    if (filename) url += `&filename=${encodeURIComponent(filename)}`;

    const response = await fetch(url, {
      method: 'DELETE'
    });

    if (!response.ok) {
      console.error('Delete record error via proxy:', await response.text());
      return false;
    }
    return true;
  } catch (error) {
    console.error('Delete record error via proxy:', error);
    return false;
  }
}

// Get files for AI embedding (scoped to company)
export async function getFilesForEmbedding(companyId?: string) {
  let query = supabase
    .from('File')
    .select('*')
    .order('created_at', { ascending: false });

  if (companyId) {
    query = query.eq('companyId', companyId);
  }

  const { data, error } = await query;

  if (error) {
    console.error('Error fetching files for embedding:', error);
    return [];
  }
  return data;
}

// Get file counts by building and folder (scoped to company)
export async function getFileCounts(companyId?: string, buildingIds?: string[]) {
  let query = supabase
    .from('File')
    .select('buildingId, folder');

  // Filter by company if provided
  if (companyId) {
    query = query.eq('companyId', companyId);
  }

  // Filter by specific building IDs if provided
  if (buildingIds && buildingIds.length > 0) {
    query = query.in('buildingId', buildingIds);
  }

  const { data, error } = await query;

  if (error) {
    console.error('Error fetching file counts:', error);
    return [];
  }

  // Aggregate counts
  // Structure: { buildingId: { total: 0, categories: { folder: count } } }
  const counts: Record<string, any> = {};

  data.forEach((file: any) => {
    const buildingId = file.buildingId || 'unknown';
    const folder = (file.folder || 'other').toLowerCase();

    if (!counts[buildingId]) {
      counts[buildingId] = { total: 0, categories: {} };
    }

    counts[buildingId].total++;
    counts[buildingId].categories[folder] = (counts[buildingId].categories[folder] || 0) + 1;
  });

  return counts;
}

// ===== DOCUMENT UPDATES OPERATIONS =====

export interface DocumentUpdate {
  document_id: string; // References File.id
  user_id: string; // References auth user
  type: 'note' | 'highlight' | 'new_file_upload';
  s3_version_id?: string;
  metadata: any;
}

// Get updates for a file
export async function getDocumentUpdates(fileId: string) {
  const { data, error } = await supabase
    .from('DocumentUpdates')
    .select('*')
    .eq('document_id', fileId)
    .order('created_at', { ascending: false });

  if (error) {
    console.error('Error fetching document updates:', error);
    return [];
  }

  return data;
}

// Create a new update
export async function createDocumentUpdate(update: DocumentUpdate) {
  const { data, error } = await supabase
    .from('DocumentUpdates')
    .insert([update])
    .select()
    .single();

  if (error) {
    console.error('Error creating document update:', error);
    return null;
  }

  return data;
}