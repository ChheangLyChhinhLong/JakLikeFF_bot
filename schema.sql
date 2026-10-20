-- Destructive store reset: run manually in the Supabase SQL editor before deploying.
-- This removes legacy catalog, stock, order, user, and settings data.
begin;

drop function if exists public.reserve_order(bigint, uuid, integer);
drop function if exists public.set_order_payment(uuid, text, text);
drop function if exists public.release_order(uuid, text);
drop function if exists public.fulfill_order(uuid);
drop function if exists public.mark_order_delivered(uuid);
drop function if exists public.available_stock_count(uuid);
drop function if exists public.sales_summary();
drop function if exists public.claim_due_order(bigint);
drop function if exists public.record_like_delivery(bigint, integer);
drop function if exists public.release_delivery_claim(bigint);
drop function if exists public.system_stats();
drop table if exists public.stock_items cascade;
drop table if exists public.products cascade;
drop table if exists public.categories cascade;
drop table if exists public.orders cascade;
drop table if exists public.package_prices cascade;
drop table if exists public.users cascade;
drop table if exists public.bot_settings cascade;

create table public.users (
    telegram_id bigint primary key,
    username text,
    first_name text not null,
    is_blocked boolean not null default false,
    created_at timestamptz not null default now()
);

create table public.package_prices (
    package_likes integer primary key check (package_likes > 0),
    price_usd numeric(10, 2) not null check (price_usd > 0),
    is_active boolean not null default true
);

insert into public.package_prices (package_likes, price_usd) values
    (220, 0.20),
    (1000, 0.50),
    (2000, 1.00),
    (3000, 1.50),
    (4000, 2.00),
    (5000, 2.50),
    (6000, 2.75),
    (7000, 3.25),
    (8000, 3.75),
    (9000, 4.25),
    (10000, 4.50),
    (20000, 8.50),
    (30000, 12.00),
    (50000, 19.00);

create table public.orders (
    id bigserial primary key,
    transaction_id text unique,
    telegram_id bigint not null references public.users(telegram_id),
    ff_uid text not null check (ff_uid ~ '^[0-9]{1,20}$'),
    total_likes integer not null check (total_likes > 0),
    amount numeric(10, 2) not null check (amount > 0),
    sent_likes integer not null default 0 check (sent_likes >= 0),
    status text not null default 'pending'
        check (status in ('pending', 'paid', 'active', 'completed', 'failed')),
    message_id bigint,
    last_run_date date,
    created_at timestamptz not null default now(),
    delivery_claimed_at timestamptz,
    check (sent_likes <= total_likes)
);

create table public.bot_settings (
    key text primary key,
    value text not null
);

create index orders_pending_created_idx
    on public.orders (created_at) where status = 'pending';
create index orders_active_delivery_idx
    on public.orders (created_at) where status = 'active';
create index orders_telegram_created_idx
    on public.orders (telegram_id, created_at desc);

alter table public.users enable row level security;
alter table public.orders enable row level security;
alter table public.package_prices enable row level security;
alter table public.bot_settings enable row level security;

create or replace function public.claim_due_order(p_order_id bigint default null)
returns setof public.orders
language plpgsql
security definer
set search_path = public
as $$
begin
    return query
    with candidate as (
        select id
        from public.orders
        where status = 'active'
          and sent_likes < total_likes
          and last_run_date is distinct from current_date
          and (delivery_claimed_at is null or delivery_claimed_at < now() - interval '10 minutes')
          and (p_order_id is null or id = p_order_id)
        order by created_at
        for update skip locked
        limit 1
    )
    update public.orders as target
       set delivery_claimed_at = now()
      from candidate
     where target.id = candidate.id
    returning target.*;
end;
$$;

create or replace function public.record_like_delivery(
    p_order_id bigint,
    p_delivered_count integer
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    updated_order public.orders;
begin
    if p_delivered_count < 1 then
        raise exception 'Delivered count must be positive';
    end if;

    update public.orders
       set sent_likes = sent_likes + p_delivered_count,
           status = case
               when sent_likes + p_delivered_count >= total_likes then 'completed'
               else 'active'
           end,
           last_run_date = current_date,
           delivery_claimed_at = null
     where id = p_order_id
       and status = 'active'
       and delivery_claimed_at is not null
       and sent_likes + p_delivered_count <= total_likes
    returning * into updated_order;

    if not found then
        raise exception 'No claimed active order can accept this delivery';
    end if;

    return jsonb_build_object(
        'id', updated_order.id,
        'sent_likes', updated_order.sent_likes,
        'total_likes', updated_order.total_likes,
        'status', updated_order.status
    );
end;
$$;

create or replace function public.system_stats()
returns jsonb
language sql
security definer
set search_path = public
as $$
    select jsonb_build_object(
        'total_orders', count(*),
        'total_revenue', coalesce(
            sum(amount) filter (where status in ('paid', 'active', 'completed')),
            0
        )
    )
    from public.orders;
$$;

create or replace function public.release_delivery_claim(p_order_id bigint)
returns void
language sql
security definer
set search_path = public
as $$
    update public.orders
       set delivery_claimed_at = null
     where id = p_order_id
       and status = 'active';
$$;

revoke all on function public.claim_due_order(bigint) from public, anon, authenticated;
revoke all on function public.record_like_delivery(bigint, integer) from public, anon, authenticated;
revoke all on function public.release_delivery_claim(bigint) from public, anon, authenticated;
revoke all on function public.system_stats() from public, anon, authenticated;
grant execute on function public.claim_due_order(bigint) to service_role;
grant execute on function public.record_like_delivery(bigint, integer) to service_role;
grant execute on function public.release_delivery_claim(bigint) to service_role;
grant execute on function public.system_stats() to service_role;
grant all on public.users, public.orders, public.package_prices, public.bot_settings to service_role;
grant usage, select on sequence public.orders_id_seq to service_role;

commit;
