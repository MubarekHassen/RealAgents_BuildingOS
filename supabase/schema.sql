create extension if not exists vector with schema extensions;

insert into storage.buckets (id, name, public)
values ('documents', 'documents', false)
on conflict (id) do nothing;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

create table if not exists public.documents (
  id text primary key,
  building_id text,
  filename text not null,
  storage_path text not null,
  mime_type text not null,
  size_bytes bigint not null,
  status text not null default 'processing' check (status in ('processing', 'ready', 'error')),
  document_summary text,
  analysis_json jsonb,
  extracted_text text,
  chunk_count integer not null default 0,
  error_message text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

drop trigger if exists set_documents_updated_at on public.documents;

create trigger set_documents_updated_at
before update on public.documents
for each row execute function public.set_updated_at();

create table if not exists public.document_chunks (
  id bigint generated always as identity primary key,
  document_id text not null references public.documents(id) on delete cascade,
  chunk_index integer not null,
  content text not null,
  token_count integer not null default 0,
  page_refs jsonb not null default '[]'::jsonb,
  embedding extensions.vector(1536) not null,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists document_chunks_document_id_idx
on public.document_chunks (document_id);

create index if not exists document_chunks_embedding_hnsw_idx
on public.document_chunks
using hnsw (embedding extensions.vector_cosine_ops);

create table if not exists public.document_questions (
  id text primary key,
  document_id text not null references public.documents(id) on delete cascade,
  question text not null,
  answer text not null,
  sources_json jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create or replace function public.match_document_chunks(
  query_embedding extensions.vector(1536),
  match_document_id text,
  match_threshold float default 0.2,
  match_count int default 8
)
returns table (
  id bigint,
  document_id text,
  chunk_index integer,
  content text,
  page_refs jsonb,
  similarity float
)
language sql
stable
as $$
  select
    dc.id,
    dc.document_id,
    dc.chunk_index,
    dc.content,
    dc.page_refs,
    1 - (dc.embedding <=> query_embedding) as similarity
  from public.document_chunks dc
  where dc.document_id = match_document_id
    and 1 - (dc.embedding <=> query_embedding) > match_threshold
  order by dc.embedding <=> query_embedding
  limit match_count;
$$;
