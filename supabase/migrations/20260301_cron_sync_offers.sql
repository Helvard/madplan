-- Schedule weekly sync of Rema 1000 offers via the sync-offers Edge Function.
-- Runs every day at 23:59 UTC.
--
-- Prerequisites: pg_cron and pg_net extensions must be enabled in the project.
-- Enable them under: Database → Extensions in the Supabase Dashboard.
--
-- To run manually:  SELECT cron.schedule(...) below
-- To remove:        SELECT cron.unschedule('sync-rema-offers');
-- To list jobs:     SELECT * FROM cron.job;
-- To see history:   SELECT * FROM cron.job_run_details ORDER BY start_time DESC LIMIT 20;

select cron.schedule(
  'sync-rema-offers',           -- unique job name
  '59 23 * * *',                -- every day at 23:59 UTC
  $$
  select net.http_post(
    url     := 'https://kbpemwwxxeeasmrcsztp.supabase.co/functions/v1/sync-offers',
    headers := jsonb_build_object(
      'Content-Type',  'application/json',
      'Authorization', 'Bearer 9c1a6e6ccc39df5e498a8194c6b08a6146f91c38448a54b3604a2f0cdfc82eff'
    ),
    body    := '{}'::jsonb
  );
  $$
);
