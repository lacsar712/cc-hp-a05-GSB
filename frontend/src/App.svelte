<script>
  let username = localStorage.getItem('herb_user') || 'processor'
  let password = 'herb123456'
  let token = localStorage.getItem('herb_token') || ''
  let role = localStorage.getItem('herb_role') || ''
  let rows = []
  let herb = '白芍'
  let tempC = 110
  let minutes = 10
  let error = ''

  let view = location.hash === '#quota' ? 'quota' : 'home'
  let quota = null
  let ledger = []
  let newLimit = 100
  let quotaMsg = ''
  let quotaError = ''

  const KIND_LABELS = {
    init: '初始化额度',
    set_limit: '设定额度',
    consume: '开炒消耗',
    reset: '零点清零',
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '请求失败')
    return data
  }

  async function enter() {
    const data = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    token = data.access_token
    role = data.role
    username = data.username
    localStorage.setItem('herb_token', token)
    localStorage.setItem('herb_role', role)
    localStorage.setItem('herb_user', username)
    await Promise.all([load(), loadQuota()])
  }

  async function load() {
    rows = await api('/api/batches')
  }

  async function loadQuota() {
    quota = await api('/api/quota')
    ledger = await api('/api/quota/ledger')
  }

  async function save() {
    error = ''
    try {
      await api('/api/batches', {
        method: 'POST',
        body: JSON.stringify({
          herb,
          steps: [{ name: '清炒', temp_c: Number(tempC), minutes: Number(minutes) }],
        }),
      })
      await Promise.all([load(), loadQuota()])
    } catch (err) {
      error = err.message
    }
  }

  async function saveQuota() {
    quotaMsg = ''
    quotaError = ''
    try {
      const data = await api('/api/quota', {
        method: 'PUT',
        body: JSON.stringify({ daily_limit: Number(newLimit) }),
      })
      quotaMsg = `本日能耗额度已设为 ${data.daily_limit} 点，下一次提交起生效`
      await loadQuota()
    } catch (err) {
      quotaError = err.message
    }
  }

  function onHashChange() {
    view = location.hash === '#quota' ? 'quota' : 'home'
    if (token && view === 'quota') loadQuota()
  }

  function leave() {
    localStorage.clear()
    token = ''
    role = ''
    quota = null
    ledger = []
  }

  function fmtTime(value) {
    return String(value).slice(0, 19).replace('T', ' ')
  }

  if (token) {
    load()
    loadQuota()
  }
</script>

<svelte:window on:hashchange={onHashChange} />

<main>
  {#if !token}
    <h1>饮片炮制记录台</h1>
    <p>炮制记录整包保存。清炒温度须在 80 到 150，时长须在 5 到 30 分钟。开炒按能耗日额度卡控。</p>
    <input bind:value={username} />
    <input type="password" bind:value={password} />
    <button on:click={enter}>登录</button>
    <p>processor / herb123456 可写；checker / check123456 只读</p>
  {:else}
    <nav class="topbar">
      <strong>饮片炮制记录台</strong>
      <a href="#/" class:active={view === 'home'}>炮制记录</a>
      <a href="#quota" class:active={view === 'quota'}>能耗额度</a>
      <span class="spacer"></span>
      <span>{username}（{role === 'writer' ? '炮制员' : '质检员'}）</span>
      <button on:click={leave}>退出</button>
    </nav>

    {#if view === 'home'}
      {#if role === 'writer'}
        <section>
          <input bind:value={herb} placeholder="饮片" />
          <input type="number" bind:value={tempC} />
          <input type="number" bind:value={minutes} />
          <button on:click={save}>写入清炒记录</button>
          {#if quota}
            <p>今日能耗：已用 {quota.used} 点 / 额度 {quota.daily_limit} 点（剩余 {quota.remaining} 点）</p>
          {/if}
          {#if error}<p class="error">{error}</p>{/if}
        </section>
      {/if}
      <ul>
        {#each rows as row}
          <li>{row.herb} · {row.verdict} · {row.reason} · 温度 {row.doc.steps[0].temp_c}</li>
        {/each}
      </ul>
    {:else}
      <h1>能耗额度</h1>
      {#if quota}
        <section>
          <h2>今日已用</h2>
          <p>{quota.quota_date}：已用 {quota.used} 点 / 本日额度 {quota.daily_limit} 点，剩余 {quota.remaining} 点</p>
        </section>
        <section>
          <h2>额度设置</h2>
          {#if role === 'writer'}
            <input type="number" min="0" bind:value={newLimit} />
            <button on:click={saveQuota}>设定本日额度</button>
            <p>额度改完后下一次提交起生效；每日零点额度清零，清零动作写入额度流水。</p>
            {#if quotaMsg}<p>{quotaMsg}</p>{/if}
            {#if quotaError}<p class="error">{quotaError}</p>{/if}
          {:else}
            <p>质检员仅可查阅已用点数与额度流水，不可改额度、不可开炒。</p>
          {/if}
        </section>
        <section>
          <h2>额度流水</h2>
          {#if ledger.length === 0}
            <p>暂无流水</p>
          {:else}
            <table>
              <thead>
                <tr><th>时间</th><th>动作</th><th>额度变化</th><th>已用变化</th><th>操作人</th></tr>
              </thead>
              <tbody>
                {#each ledger as entry}
                  <tr>
                    <td>{fmtTime(entry.created_at)}</td>
                    <td>{KIND_LABELS[entry.change_type] || entry.change_type}</td>
                    <td>
                      {#if entry.old_limit === null && entry.new_limit === null}
                        —
                      {:else}
                        {entry.old_limit ?? '—'} → {entry.new_limit ?? '—'}
                      {/if}
                    </td>
                    <td>{entry.used_before} → {entry.used_after}</td>
                    <td>{entry.operator}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </section>
      {/if}
    {/if}
  {/if}
</main>

<style>
  main { font-family: sans-serif; max-width: 720px; margin: 24px auto; color: #3f2f1f; }
  h1 { color: #7c2d12; }
  h2 { color: #7c2d12; font-size: 18px; margin-bottom: 6px; }
  input { margin-right: 8px; padding: 6px; }
  section { margin-bottom: 20px; }
  .topbar { display: flex; align-items: center; gap: 14px; padding: 10px 0; border-bottom: 2px solid #7c2d12; margin-bottom: 18px; }
  .topbar a { color: #7c2d12; text-decoration: none; padding: 2px 6px; }
  .topbar a.active { font-weight: bold; border-bottom: 2px solid #7c2d12; }
  .spacer { flex: 1; }
  .error { color: #b91c1c; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid #d6c8b8; padding: 6px 8px; text-align: left; font-size: 14px; }
  th { background: #f5ede3; }
</style>
