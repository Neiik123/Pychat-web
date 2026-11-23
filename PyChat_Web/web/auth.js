/* PyChat Web authentication bootstrap. Supabase public anon key is safe in the browser;
   service-role and encryption secrets remain exclusively in the backend environment. */
(() => {
  let resolveReady, rejectReady;
  window.pychatAuthReady = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
  const waitForBody = () => new Promise(resolve => {
    if (document.body) return resolve();
    document.addEventListener('DOMContentLoaded', resolve, { once: true });
  });

  (async () => {
    await waitForBody();
    const root = document.createElement('div');
    root.id = 'pychat-auth';
    root.innerHTML = `
      <style>
        #pychat-auth{position:fixed;inset:0;z-index:100000;background:#08090d;color:#f4f5f7;display:flex;align-items:center;justify-content:center;padding:20px;font:15px/1.5 system-ui,sans-serif}
        #pychat-auth .auth-card{width:min(420px,100%);background:#151821;border:1px solid #343946;border-radius:18px;padding:28px;box-shadow:0 24px 80px #0009}
        #pychat-auth h1{font-size:32px;margin:0 0 5px}#pychat-auth p{color:#aab0bd;margin:0 0 20px}
        #pychat-auth label{display:block;margin:12px 0 5px;color:#d9dce3}#pychat-auth input{width:100%;padding:12px;border-radius:9px;border:1px solid #424958;background:#0d1017;color:white;box-sizing:border-box}
        #pychat-auth button{width:100%;margin-top:14px;padding:12px;border:0;border-radius:9px;background:#3f8be0;color:white;font-weight:700;cursor:pointer}
        #pychat-auth button.secondary{background:#2b303b}#pychat-auth .auth-msg{min-height:22px;margin-top:12px;font-size:13px;color:#f0c879}
        #pychat-userbar{position:fixed;z-index:99999;right:12px;top:12px;display:flex;gap:9px;align-items:center;background:#171a22eF;color:#fff;border:1px solid #444;padding:7px 10px;border-radius:10px;font:12px system-ui}
        #pychat-userbar button{background:#343b49;color:white;border:0;border-radius:7px;padding:6px 9px;cursor:pointer}
      </style>
      <div class="auth-card"><h1>PyChat</h1><p>Sign in to sync your characters and conversations.</p>
        <form id="pychat-auth-form"><label for="pychat-email">Email</label><input id="pychat-email" type="email" autocomplete="email" required>
        <label for="pychat-password">Password</label><input id="pychat-password" type="password" autocomplete="current-password" minlength="8" required>
        <button id="pychat-submit" type="submit">Sign in</button></form>
        <button class="secondary" id="pychat-toggle" type="button">Create an account</button>
        <button class="secondary" id="pychat-forgot" type="button">Forgot password?</button>
        <div class="auth-msg" id="pychat-auth-msg" role="status"></div>
      </div>`;
    document.body.appendChild(root);

    const msg = s => { document.getElementById('pychat-auth-msg').textContent = s || ''; };
    try {
      const cfgRes = await fetch('/api/config');
      if (!cfgRes.ok) throw new Error((await cfgRes.text()).slice(0, 250));
      const cfg = await cfgRes.json();
      if (!window.supabase?.createClient) throw new Error('Supabase client library did not load.');
      const client = window.supabase.createClient(cfg.supabaseUrl, cfg.supabaseAnonKey);
      window.pychatSupabase = client;
      let creating = false, recoveryMode = false;
      const toggle = document.getElementById('pychat-toggle');
      const forgot = document.getElementById('pychat-forgot');
      const submit = document.getElementById('pychat-submit');
      const passwordInput = document.getElementById('pychat-password');
      toggle.onclick = () => {
        creating = !creating;
        submit.textContent = creating ? 'Create account' : 'Sign in';
        toggle.textContent = creating ? 'I already have an account' : 'Create an account';
        passwordInput.autocomplete = creating ? 'new-password' : 'current-password';
        msg('');
      };
      forgot.onclick = async () => {
        const email = document.getElementById('pychat-email').value.trim();
        if (!email) { msg('Enter your email address first.'); return; }
        forgot.disabled = true; msg('Sending password reset email…');
        try {
          const result = await client.auth.resetPasswordForEmail(email, {redirectTo: location.origin});
          msg(result.error ? result.error.message : 'If an account exists for that address, a password reset email has been sent.');
        } catch (error) { msg(error?.message || String(error)); }
        finally { forgot.disabled = false; }
      };
      document.getElementById('pychat-auth-form').onsubmit = async e => {
        e.preventDefault(); submit.disabled = true; msg('Please wait…');
        const email = document.getElementById('pychat-email').value.trim();
        const password = passwordInput.value;
        try {
          if (recoveryMode) {
            const result = await client.auth.updateUser({password});
            if (result.error) { msg(result.error.message); return; }
            recoveryMode = false;
            const current = await client.auth.getSession();
            if (current.data.session) enter(current.data.session);
            msg('Password updated. Loading PyChat…');
            return;
          }
          const result = creating ? await client.auth.signUp({email, password}) : await client.auth.signInWithPassword({email, password});
          if (result.error) { msg(result.error.message); return; }
          if (creating && !result.data.session) { msg('Account created. Check your email to confirm it, then sign in.'); return; }
          msg('Signed in. Loading PyChat…');
        } catch (error) { msg(error?.message || String(error)); }
        finally { submit.disabled = false; }
      };

      const attachToken = token => {
        window.pychatAccessToken = token || '';
        if (!window.pychatFetchPatched) {
          const originalFetch = window.fetch.bind(window);
          window.fetch = (input, init = {}) => {
            const url = typeof input === 'string' ? input : input.url;
            if ((url === '/api/proxy' || url === '/api/models') && typeof init.body === 'string') {
              try {
                const payload = JSON.parse(init.body);
                const target = new URL(payload.url || '');
                const host = target.hostname.toLowerCase();
                const local = host === 'localhost' || host === '127.0.0.1' || host === '::1' ||
                  /^10\./.test(host) || /^192\.168\./.test(host) ||
                  /^172\.(1[6-9]|2[0-9]|3[01])\./.test(host);
                if (local) {
                  // Local Ollama is reached through the optional loopback bridge;
                  // normal cloud API requests continue through the authenticated backend.
                  return originalFetch('http://127.0.0.1:8768' + url, {
                    ...init,
                    headers: {'Content-Type': 'application/json'},
                    mode: 'cors'
                  });
                }
              } catch (_) {}
            }
            if (url.startsWith('/api/') && url !== '/api/config' && window.pychatAccessToken) {
              const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
              headers.set('Authorization', 'Bearer ' + window.pychatAccessToken);
              init = {...init, headers};
            }
            return originalFetch(input, init);
          };
          window.pychatFetchPatched = true;
        }
      };
      const enter = session => {
        if (!session?.access_token) return;
        attachToken(session.access_token);
        root.style.display = 'none';
        let bar = document.getElementById('pychat-userbar');
        if (!bar) { bar = document.createElement('div'); bar.id = 'pychat-userbar'; document.body.appendChild(bar); }
        bar.innerHTML = `<span>${String(session.user.email || 'Signed in').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}</span><button type="button" id="pychat-signout">Sign out</button>`;
        bar.querySelector('button').onclick = async () => { await client.auth.signOut(); location.reload(); };
        resolveReady(session);
      };
      client.auth.onAuthStateChange((event, session) => {
        if (event === 'PASSWORD_RECOVERY') {
          recoveryMode = true;
          creating = false;
          root.style.display = 'flex';
          submit.textContent = 'Update password';
          toggle.style.display = 'none';
          forgot.style.display = 'none';
          passwordInput.value = '';
          passwordInput.autocomplete = 'new-password';
          msg('Enter your new password.');
          return;
        }
        if (session) enter(session);
        else { attachToken(''); root.style.display = 'flex'; }
      });
      const {data, error} = await client.auth.getSession();
      if (error) throw error;
      if (data.session && !recoveryMode) enter(data.session);
      else if (!recoveryMode) msg('Sign in or create an account to continue.');
    } catch (e) {
      msg('Setup error: ' + (e?.message || String(e)));
      rejectReady(e);
    }
  })();
})();
