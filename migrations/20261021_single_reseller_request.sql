begin;

create or replace function public.claim_due_order(p_order_id bigint default null)
returns setof public.orders
language plpgsql
security definer
set search_path = public
as $$
begin
    return query
    with candidate as (
        select current_order.id
        from public.orders as current_order
        where current_order.status = 'active'
          and current_order.sent_likes = 0
          and current_order.sent_likes < current_order.total_likes
          and current_order.last_run_date is distinct from current_date
          and current_order.delivery_claimed_at is null
          and (p_order_id is null or current_order.id = p_order_id)
          and not exists (
              select 1
              from public.orders as earlier_order
              where earlier_order.ff_uid = current_order.ff_uid
                and (earlier_order.created_at, earlier_order.id)
                    < (current_order.created_at, current_order.id)
                and (
                    (
                        earlier_order.status = 'active'
                        and earlier_order.sent_likes < earlier_order.total_likes
                    )
                    or earlier_order.last_run_date = current_date
                )
          )
        order by current_order.created_at, current_order.id
        for update of current_order skip locked
        limit 1
    )
    update public.orders as target
       set delivery_claimed_at = now()
      from candidate
     where target.id = candidate.id
    returning target.*;
end;
$$;

commit;
