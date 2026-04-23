-- Double-opt-in + unsubscribe flow for newsletter signups.
--
-- Flow:
--   1. Client inserts row with just {email}. RLS allows anonymous insert.
--   2. BEFORE INSERT trigger `auto_confirm_subscriber` branches:
--        - if user_id is set    -> confirmed_at = now() (magic-link signup)
--        - if user_id is null   -> leave unconfirmed, generate confirm_token
--   3. AFTER INSERT trigger sends a confirmation email via pg_net -> Resend
--      with a link to confirm.html?token=... (only fires for unconfirmed rows).
--   4. confirm.html calls RPC confirm_subscription(token) which flips
--      confirmed_at and clears the token. Only then does newsletter.py
--      include the address.
--   5. Every newsletter email carries an unsubscribe link to
--      unsubscribe.html?token=... which calls RPC unsubscribe(token) and
--      sets unsubscribed_at.

-- Default confirmation_token for unauthed signups. `pgcrypto` is already
-- enabled from schema.sql.
alter table public.subscribers
    alter column confirmation_token set default encode(gen_random_bytes(24), 'hex');

-- Rewrite auto_confirm trigger: keep auto-confirm for user_id signups,
-- but do NOT fill a confirmation_token (it has to stay NULL so the
-- AFTER-INSERT mailer knows not to bother).
create or replace function public.auto_confirm_subscriber()
returns trigger language plpgsql as $$
begin
    if new.user_id is not null then
        if new.confirmed_at is null then
            new.confirmed_at := now();
        end if;
        new.confirmation_token := null;   -- not needed for authed flow
    end if;
    return new;
end;
$$;

drop trigger if exists trg_subscribers_autoconfirm on public.subscribers;
create trigger trg_subscribers_autoconfirm before insert on public.subscribers
    for each row execute function public.auto_confirm_subscriber();

-- AFTER INSERT: if the row is unconfirmed (user didn't sign in, so we need
-- to prove they own the email), fire off a confirmation email.
create or replace function public.send_confirmation_email()
returns trigger language plpgsql security definer set search_path = public, private as $$
declare
    api_key  text;
    site_url text;
    confirm_url text;
    html     text;
    txt      text;
begin
    -- Only email unauthed, unconfirmed, not-yet-unsubscribed rows.
    if new.confirmed_at is not null or new.confirmation_token is null or new.unsubscribed_at is not null then
        return new;
    end if;

    select value into api_key  from private.settings where key = 'resend_api_key';
    select value into site_url from private.settings where key = 'site_url';
    if api_key is null or site_url is null then return new; end if;

    confirm_url := site_url || 'confirm.html?token=' || new.confirmation_token;

    html :=
        '<p>Thanks for subscribing to the weekly Tesla robotaxi scaling update.</p>' ||
        '<p>Click to confirm your email:</p>' ||
        '<p><a href="' || confirm_url ||
        '" style="display:inline-block;background:#4ea3ff;color:#0b0d10;padding:10px 18px;' ||
        'border-radius:6px;text-decoration:none;font-weight:600">Confirm subscription</a></p>' ||
        '<p style="color:#8a94a3;font-size:12px">If that button doesn''t work, paste this URL: ' ||
        '<br>' || confirm_url || '</p>' ||
        '<p style="color:#8a94a3;font-size:12px">If you didn''t sign up, just ignore this ' ||
        'email — nothing will happen and we won''t send you anything.</p>';

    txt := 'Confirm your subscription: ' || confirm_url ||
           e'\n\nIf you didn''t sign up, ignore this email.';

    perform net.http_post(
        url := 'https://api.resend.com/emails',
        headers := jsonb_build_object(
            'Authorization', 'Bearer ' || api_key,
            'Content-Type',  'application/json'
        ),
        body := jsonb_build_object(
            'from',    'Robotaxi Predictor <onboarding@resend.dev>',
            'to',      jsonb_build_array(new.email),
            'subject', 'Confirm your robotaxi newsletter subscription',
            'html',    html,
            'text',    txt
        )
    );
    return new;
end;
$$;

drop trigger if exists trg_subscribers_send_confirm on public.subscribers;
create trigger trg_subscribers_send_confirm after insert on public.subscribers
    for each row execute function public.send_confirmation_email();

-- RPC: confirm subscription by token. Exposed to anon so confirm.html can
-- call it without auth. Returns jsonb so the client can show a useful message.
create or replace function public.confirm_subscription(p_token text)
returns jsonb language plpgsql security definer set search_path = public as $$
declare
    updated_email text;
begin
    if p_token is null or length(p_token) < 16 then
        return jsonb_build_object('ok', false, 'reason', 'invalid-token');
    end if;

    update public.subscribers
       set confirmed_at = coalesce(confirmed_at, now()),
           confirmation_token = null
     where confirmation_token = p_token
     returning email into updated_email;

    if updated_email is null then
        return jsonb_build_object('ok', false, 'reason', 'unknown-or-already-confirmed');
    end if;

    return jsonb_build_object('ok', true, 'email', updated_email);
end;
$$;

-- RPC: unsubscribe by token. Stays reversible — we only set unsubscribed_at,
-- so the row is still there if the person wants to re-confirm later.
create or replace function public.unsubscribe(p_token text)
returns jsonb language plpgsql security definer set search_path = public as $$
declare
    updated_email text;
begin
    if p_token is null or length(p_token) < 16 then
        return jsonb_build_object('ok', false, 'reason', 'invalid-token');
    end if;

    update public.subscribers
       set unsubscribed_at = coalesce(unsubscribed_at, now())
     where unsubscribe_token = p_token
     returning email into updated_email;

    if updated_email is null then
        return jsonb_build_object('ok', false, 'reason', 'unknown-token');
    end if;

    return jsonb_build_object('ok', true, 'email', updated_email);
end;
$$;

grant execute on function public.confirm_subscription(text) to anon, authenticated;
grant execute on function public.unsubscribe(text) to anon, authenticated;
