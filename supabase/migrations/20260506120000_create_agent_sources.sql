create table if not exists public.agent_sources (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  listing_url text not null unique,
  enabled boolean not null default true,
  excluded_keywords text[] not null default array[]::text[],
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists agent_sources_enabled_idx
  on public.agent_sources (enabled)
  where enabled is true;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists set_agent_sources_updated_at on public.agent_sources;

create trigger set_agent_sources_updated_at
before update on public.agent_sources
for each row
execute function public.set_updated_at();

alter table public.agent_sources enable row level security;

create table if not exists public.agent_directory (
  id uuid primary key default gen_random_uuid(),
  agent_name text not null,
  owned_website_url text not null unique,
  status text,
  evidence_or_note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists agent_directory_status_idx
  on public.agent_directory (status);

drop trigger if exists set_agent_directory_updated_at on public.agent_directory;

create trigger set_agent_directory_updated_at
before update on public.agent_directory
for each row
execute function public.set_updated_at();

alter table public.agent_directory enable row level security;
