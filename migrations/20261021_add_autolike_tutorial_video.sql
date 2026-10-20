insert into public.bot_settings (key, value)
values ('autolike_tutorial_video_url', 'https://t.me/LongStores_kh/134')
on conflict (key) do update
set value = excluded.value;
