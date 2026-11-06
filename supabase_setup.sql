-- CrawlTool: bảng đồng bộ kênh theo dõi. Chủ app chạy 1 lần: Supabase -> SQL Editor -> dán -> Run (chạy lại không sao).
-- Tài khoản người dùng: Authentication -> Users -> Add user (tích Auto Confirm). Nên tắt "Allow new users to sign up".
create table if not exists public.follows (
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  key text not null,
  data jsonb not null default '{}'::jsonb,
  deleted boolean not null default false,
  updated bigint not null default 0,
  primary key (user_id, key)
);
alter table public.follows enable row level security;
drop policy if exists "crawltool_own_rows" on public.follows;
create policy "crawltool_own_rows" on public.follows for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
