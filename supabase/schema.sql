-- Schema for the community / forum feature of tslaRobotaxiPredictor.
-- Applied via Supabase Management API's /database/query endpoint.
--
-- Tables:
--   profiles    public profile per auth user (display name + X/LinkedIn)
--   posts       combined top-level posts + threaded replies, plus optional feature-request tag
--   votes       one row per (user, post) up/down vote
--   subscribers email newsletter signup (standalone + auto-subscribe via comment form)
--
-- Thread depth is capped at 5 via a CHECK on posts.depth.
-- Vote totals are maintained via an aggregate view (posts_with_score).

create extension if not exists "pgcrypto";

-- =============================================================================
-- profiles
-- =============================================================================
create table if not exists public.profiles (
    id              uuid primary key references auth.users(id) on delete cascade,
    display_name    text not null check (char_length(display_name) between 1 and 50),
    x_handle        text check (x_handle is null or x_handle ~ '^[A-Za-z0-9_]{1,15}$'),
    linkedin_url    text check (linkedin_url is null or linkedin_url ~ '^https?://([a-z]+\.)?linkedin\.com/'),
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- =============================================================================
-- posts  (unified: top-level threads + replies)
-- =============================================================================
create table if not exists public.posts (
    id                  uuid primary key default gen_random_uuid(),
    author_id           uuid not null references public.profiles(id) on delete cascade,
    parent_id           uuid references public.posts(id) on delete cascade, -- null = top-level
    depth               smallint not null default 0 check (depth between 0 and 5),
    title               text check (
        (parent_id is null and title is not null and char_length(title) between 3 and 140)
        or (parent_id is not null and title is null)
    ),
    body                text not null check (char_length(body) between 1 and 8000),
    is_feature_request  boolean not null default false,
    deleted_at          timestamptz,
    created_at          timestamptz not null default now()
);

create index if not exists idx_posts_parent on public.posts (parent_id);
create index if not exists idx_posts_author on public.posts (author_id);
create index if not exists idx_posts_created on public.posts (created_at desc);
create index if not exists idx_posts_feature_request on public.posts (is_feature_request) where is_feature_request = true;

-- =============================================================================
-- votes
-- =============================================================================
create table if not exists public.votes (
    post_id     uuid not null references public.posts(id) on delete cascade,
    user_id     uuid not null references public.profiles(id) on delete cascade,
    value       smallint not null check (value in (-1, 1)),
    created_at  timestamptz not null default now(),
    primary key (post_id, user_id)
);

create index if not exists idx_votes_post on public.votes (post_id);

-- Aggregate view: post with computed score.
create or replace view public.posts_with_score as
select
    p.*,
    coalesce(sum(v.value), 0)::int as score,
    count(v.*) filter (where v.value = 1)::int as upvotes,
    count(v.*) filter (where v.value = -1)::int as downvotes
from public.posts p
left join public.votes v on v.post_id = p.id
group by p.id;

-- =============================================================================
-- subscribers
-- =============================================================================
create table if not exists public.subscribers (
    email             text primary key check (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    user_id           uuid references public.profiles(id) on delete set null,
    confirmation_token text,
    confirmed_at      timestamptz,
    unsubscribe_token text not null default encode(gen_random_bytes(24), 'hex'),
    unsubscribed_at   timestamptz,
    created_at        timestamptz not null default now()
);

create index if not exists idx_subscribers_confirmed on public.subscribers (confirmed_at) where unsubscribed_at is null;

-- =============================================================================
-- Trigger: auto-bump profiles.updated_at
-- =============================================================================
create or replace function public.touch_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists trg_profiles_touch on public.profiles;
create trigger trg_profiles_touch before update on public.profiles
    for each row execute function public.touch_updated_at();

-- =============================================================================
-- Trigger: auto-create a minimal profile row when a user signs up.
-- Display name defaults to the email local-part, user can edit later.
-- =============================================================================
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
declare
    fallback_name text;
begin
    fallback_name := coalesce(
        new.raw_user_meta_data->>'display_name',
        new.raw_user_meta_data->>'full_name',
        new.raw_user_meta_data->>'name',
        split_part(new.email, '@', 1)
    );
    insert into public.profiles (id, display_name)
    values (new.id, substring(fallback_name for 50))
    on conflict (id) do nothing;
    return new;
end;
$$;

drop trigger if exists trg_on_auth_user_created on auth.users;
create trigger trg_on_auth_user_created after insert on auth.users
    for each row execute function public.handle_new_user();

-- =============================================================================
-- Trigger: derive depth from parent + cap at 5
-- =============================================================================
create or replace function public.set_post_depth()
returns trigger language plpgsql as $$
begin
    if new.parent_id is null then
        new.depth := 0;
    else
        select least(depth + 1, 5) into new.depth
        from public.posts
        where id = new.parent_id;
        if new.depth is null then
            new.depth := 0;
        end if;
    end if;
    return new;
end;
$$;

drop trigger if exists trg_posts_set_depth on public.posts;
create trigger trg_posts_set_depth before insert on public.posts
    for each row execute function public.set_post_depth();

-- =============================================================================
-- RLS
-- =============================================================================
alter table public.profiles    enable row level security;
alter table public.posts       enable row level security;
alter table public.votes       enable row level security;
alter table public.subscribers enable row level security;

-- profiles: readable by everyone, writable only by the owner
drop policy if exists profiles_select on public.profiles;
create policy profiles_select on public.profiles for select using (true);

drop policy if exists profiles_insert on public.profiles;
create policy profiles_insert on public.profiles for insert with check (auth.uid() = id);

drop policy if exists profiles_update on public.profiles;
create policy profiles_update on public.profiles for update using (auth.uid() = id) with check (auth.uid() = id);

-- posts: readable by everyone; writable by the logged-in author; soft-delete only
drop policy if exists posts_select on public.posts;
create policy posts_select on public.posts for select using (true);

drop policy if exists posts_insert on public.posts;
create policy posts_insert on public.posts for insert with check (auth.uid() = author_id);

drop policy if exists posts_update on public.posts;
create policy posts_update on public.posts for update using (auth.uid() = author_id) with check (auth.uid() = author_id);

-- (we don't expose a DELETE policy — admin deletion happens via service_role / admin UI)

-- votes: readable by everyone; writable by the logged-in user only for their own row
drop policy if exists votes_select on public.votes;
create policy votes_select on public.votes for select using (true);

drop policy if exists votes_insert on public.votes;
create policy votes_insert on public.votes for insert with check (auth.uid() = user_id);

drop policy if exists votes_update on public.votes;
create policy votes_update on public.votes for update using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists votes_delete on public.votes;
create policy votes_delete on public.votes for delete using (auth.uid() = user_id);

-- subscribers: anyone can insert their own email; reads/updates only via service_role
drop policy if exists subscribers_insert on public.subscribers;
create policy subscribers_insert on public.subscribers for insert with check (true);

-- =============================================================================
-- Grants for the Data API roles
-- =============================================================================
grant usage on schema public to anon, authenticated;
grant select on public.profiles, public.posts, public.votes, public.posts_with_score to anon, authenticated;
grant insert, update on public.profiles to authenticated;
grant insert, update on public.posts to authenticated;
grant insert, update, delete on public.votes to authenticated;
grant insert on public.subscribers to anon, authenticated;
