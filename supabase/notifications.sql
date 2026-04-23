-- Fires on every INSERT into public.posts. Uses pg_net to POST to Resend's
-- /emails endpoint so the site owner (yusufh@gmail.com) gets a real-time
-- notification for every new thread or reply.
--
-- Secrets are stored as Postgres database settings (current_setting) so we
-- don't have to leak them into the public schema.

create extension if not exists pg_net;

-- The following two ALTER DATABASE commands are run once with service_role
-- privileges via the Management API, setting the Resend API key and owner
-- address as DB-wide settings readable from any function:
--   alter database postgres set app.resend_api_key = 're_...';
--   alter database postgres set app.notify_email   = 'yusufh@gmail.com';
--   alter database postgres set app.site_url       = 'https://yusufhgmail.github.io/tslaRobotaxiPredictor/';

create or replace function public.notify_new_post()
returns trigger language plpgsql security definer as $$
declare
    api_key      text;
    to_email     text;
    site_url     text;
    author_name  text;
    subj         text;
    body_html    text;
    body_text    text;
    is_reply     boolean;
begin
    -- Silently skip if settings are missing (e.g. before one-time configure).
    begin
        api_key  := current_setting('app.resend_api_key', true);
        to_email := current_setting('app.notify_email',   true);
        site_url := coalesce(current_setting('app.site_url', true), '');
    exception when others then
        return new;
    end;
    if api_key is null or api_key = '' or to_email is null or to_email = '' then
        return new;
    end if;

    is_reply := new.parent_id is not null;

    select display_name into author_name
    from public.profiles where id = new.author_id;
    author_name := coalesce(author_name, '(unknown)');

    subj := case when is_reply
                 then '[Robotaxi] Reply from ' || author_name
                 else '[Robotaxi] New post: ' || coalesce(new.title, '(untitled)')
            end;

    body_text := author_name || ' posted:' || e'\n\n' ||
                 coalesce(new.title || e'\n\n', '') ||
                 new.body || e'\n\n' ||
                 'Open: ' || site_url || 'community.html';

    body_html :=
        '<p><b>' || replace(replace(author_name, '<', '&lt;'), '>', '&gt;') || '</b> posted' ||
        case when is_reply then ' a reply' else '' end || ':</p>' ||
        case when new.title is not null
             then '<h3>' || replace(replace(new.title, '<', '&lt;'), '>', '&gt;') || '</h3>' else '' end ||
        '<div style="white-space:pre-wrap;border-left:3px solid #4ea3ff;padding:8px 12px;background:#f5f7fa;border-radius:4px">' ||
        replace(replace(new.body, '<', '&lt;'), '>', '&gt;') ||
        '</div>' ||
        case when new.is_feature_request
             then '<p style="color:#4ea3ff"><b>Tagged:</b> feature request</p>' else '' end ||
        '<p><a href="' || site_url || 'community.html">Open community page</a></p>';

    perform net.http_post(
        url := 'https://api.resend.com/emails',
        headers := jsonb_build_object(
            'Authorization', 'Bearer ' || api_key,
            'Content-Type',  'application/json'
        ),
        body := jsonb_build_object(
            'from',    'Robotaxi Community <onboarding@resend.dev>',
            'to',      jsonb_build_array(to_email),
            'subject', subj,
            'html',    body_html,
            'text',    body_text
        )
    );
    return new;
end;
$$;

drop trigger if exists trg_posts_notify on public.posts;
create trigger trg_posts_notify after insert on public.posts
    for each row execute function public.notify_new_post();
