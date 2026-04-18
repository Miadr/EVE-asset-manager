<template>
  <el-container class="layout-container">
    <el-header class="header">
      <div class="logo">
        <el-icon class="logo-icon"><Platform /></el-icon>
        <span class="logo-text">EVE 资产管理系统 V1.1</span>
        <span class="author-credits">By Sep. (Powered by Gemini)</span>
      </div>
      
      <div class="top-search-area">
        <div class="filter-group">
          <el-select v-model="query.owner_ids" multiple collapse-tags collapse-tags-tooltip placeholder="所有者" class="filter-item owner-select" @change="handleSearch" clearable>
            <el-option v-for="o in ownerOptions" :key="o.owner_id" :label="o.name" :value="o.owner_id" />
          </el-select>
          <el-select v-model="query.category_id" placeholder="物品分类" class="filter-item cat-select" @change="handleSearch" clearable filterable>
            <el-option v-for="c in categoryOptions" :key="c.categoryID" :label="c.categoryName" :value="c.categoryID" />
          </el-select>
        </div>

        <el-input v-model="query.q" placeholder="搜索物品名、类型、拼音..." clearable @keyup.enter="handleSearch" class="main-search-input" size="large">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>

        <el-checkbox v-model="query.include_fitted" @change="handleSearch" label="全局(含装配)" border class="search-checkbox" />
        
        <el-button type="primary" size="large" @click="handleSearch">搜索</el-button>
      </div>

      <div class="actions">
        <el-button class="console-btn" :icon="Menu" @click="controlPanelVisible = true">控制台</el-button>
      </div>
    </el-header>

    <el-container style="height: calc(100vh - 70px);">
      <el-aside width="280px" class="sidebar">
        <div class="sidebar-header">
          <el-input v-model="locationFilterText" placeholder="过滤地点..." prefix-icon="Search" clearable size="small" class="sidebar-input" />
        </div>
        <el-scrollbar>
          <ul class="location-list">
            <li :class="['location-item', { active: !query.location_name }]" @click="selectLocation(null)">
              <div class="loc-left"><el-icon><Location /></el-icon> <span>全部地点</span></div>
            </li>
            <li v-for="loc in filteredLocations" :key="loc.location_name" :class="['location-item', { active: query.location_name === loc.location_name }]" @click="selectLocation(loc.location_name)">
              <span class="loc-name" :title="loc.location_name">{{ loc.location_name }}</span>
              <span class="loc-count">{{ loc.count }}</span>
            </li>
          </ul>
        </el-scrollbar>
      </el-aside>

      <el-main class="main-content">
        <div class="toolbar">
          <div class="stat-group">
            <span class="stat-item">找到 <span class="highlight">{{ total }}</span> 条记录</span>
            <el-divider direction="vertical" v-if="total > 0"/>
            <el-popover v-if="total > 0" placement="bottom" :width="300" trigger="hover" popper-class="dark-popover" :disabled="statistics.length <= 1">
              <template #reference>
                <span class="stat-item pointer">
                  物品总数 <span class="highlight">{{ formatQty(totalQuantity) }}</span>
                  <el-icon v-if="statistics.length > 1" class="more-icon"><ArrowDown /></el-icon>
                </span>
              </template>
              <div class="stat-list">
                <div class="stat-title">物品统计 (Top {{ statistics.length > 50 ? 50 : statistics.length }})</div>
                <div v-for="(stat, index) in statistics" :key="index" class="stat-row">
                  <span class="stat-name">{{ stat.name }}</span>
                  <span class="stat-val">{{ formatQty(stat.quantity) }}</span>
                </div>
              </div>
            </el-popover>
          </div>
        </div>

        <el-table 
          :data="tableData" 
          style="width: 100%" 
          v-loading="loading"
          stripe
          class="asset-table"
          height="calc(100vh - 160px)"
          :row-style="{ height: '80px' }" 
        >
          <el-table-column min-width="1" class-name="spacer-col" />

          <el-table-column label="物品" width="140" align="right" sortable sort-by="quantity">
             <template #default="scope">
               <div class="icon-col">
                 <span v-if="!scope.row.is_singleton && scope.row.quantity > 1" class="qty-modern">{{ formatQty(scope.row.quantity) }}</span>
                 <div class="img-box">
                   <el-image :src="`https://image.evepc.163.com/Type/${scope.row.type_id}_64.png`" loading="lazy" class="main-icon" />
                   <div v-if="scope.row.is_blueprint" class="bp-tag">BP</div>
                 </div>
               </div>
             </template>
          </el-table-column>

          <el-table-column label="详情" min-width="340" sortable sort-by="typeName">
             <template #default="scope">
               <div class="detail-col">
                 <div class="primary-row">
                   <span class="item-name" :class="{ 'custom-highlight': scope.row.custom_name }">
                     {{ scope.row.custom_name || scope.row.typeName }}
                   </span>
                   <span v-if="scope.row.custom_name" class="real-name">({{ scope.row.typeName }})</span>
                 </div>
                 <div class="meta-row">
                   <el-tag size="small" effect="plain" class="cat-tag">{{ scope.row.groupName }}</el-tag>
                   <el-tag v-if="scope.row.is_ship_fitted" type="danger" size="small" effect="dark" class="status-tag">装配中</el-tag>
                 </div>
               </div>
             </template>
          </el-table-column>

          <el-table-column label="位置 / 容器" min-width="340" sortable sort-by="location_name">
            <template #default="scope">
               <div class="location-col">
                 <div class="loc-text" :title="scope.row.location_name">{{ scope.row.location_name }}</div>
                 <div v-if="scope.row.parent_container_name" class="container-hint">
                   <el-icon><Box /></el-icon> 
                   <span>{{ scope.row.parent_container_name }}</span>
                 </div>
               </div>
            </template>
          </el-table-column>

          <el-table-column label="归属" width="220" sortable sort-by="owner_name">
            <template #default="scope">
              <div class="owner-col">
                <el-avatar :size="36" :src="getOwnerIcon(scope.row.owner_id, scope.row.is_corp)" />
                <span class="owner-text">{{ scope.row.owner_name }}</span>
              </div>
            </template>
          </el-table-column>

          <el-table-column width="80" align="center">
            <template #default="scope">
              <el-button v-if="canOpen(scope.row)" type="primary" link icon="View" @click="openContainer(scope.row)">查看</el-button>
            </template>
          </el-table-column>

          <el-table-column min-width="1" class-name="spacer-col" />
        </el-table>

        <div class="footer-bar">
          <span class="status-text">
            本页 {{ tableData.length }} 条记录
            <el-divider direction="vertical" />
            最近资产刷新时间：{{ dbMTime }}
          </span>
          <el-pagination background small layout="prev, pager, next, jumper, sizes" :total="total" v-model:current-page="query.page" :page-size="query.limit" :page-sizes="[100, 200, 500]" @current-change="loadData" @size-change="loadData" />
        </div>
      </el-main>
    </el-container>

    <el-drawer v-model="controlPanelVisible" title="系统控制台" size="450px" custom-class="dark-drawer">
      <div class="panel-section">
        <h3>维护工具</h3>
        <el-button type="danger" plain size="small" style="width:100%; margin-bottom:10px" @click="clearLoginCache">第一步：清除网易登录缓存</el-button>
        <div style="margin-bottom:8px; text-align:center">
          <a :href="authLink" target="_blank" class="auth-link-btn">第二步：获取 ESI 授权链接</a>
        </div>
        <el-input v-model="authUrlInput" placeholder="第三步：粘贴跳转后的 URL..." :rows="2" type="textarea" />
        <el-button type="primary" style="margin-top:8px; width:100%" @click="submitAuth">提交验证</el-button>
      </div>
      <div class="panel-section">
        <h3>后台处理状态看板</h3>
        
        <div class="sync-status-row">
          <el-button type="success" @click="syncTask('assets')" :loading="syncStatus.assets.running" :disabled="syncStatus.assets.running" style="width: 100%; justify-content: flex-start; text-align:left;">
            【同步资产】拉取已授权角色与军团资产信息...
          </el-button>
          <div class="status-monitor" :class="{active: syncStatus.assets.running}" :title="syncStatus.assets.text">
             <el-icon v-if="syncStatus.assets.running" class="is-loading" style="margin-right:4px;"><Loading /></el-icon>
             <el-icon v-else style="margin-right:4px;"><Check /></el-icon>
             {{ syncStatus.assets.text }}
          </div>
        </div>

        <div class="sync-status-row">
          <el-button type="warning" @click="syncTask('universe')" :loading="syncStatus.universe.running" :disabled="syncStatus.universe.running" style="width: 100%; justify-content: flex-start; text-align:left;">
            【更新基础物品库】从官方ESI接口更新物品数据库...
          </el-button>
          <div class="status-monitor" :class="{active: syncStatus.universe.running}" :title="syncStatus.universe.text">
             <el-icon v-if="syncStatus.universe.running" class="is-loading" style="margin-right:4px;"><Loading /></el-icon>
             <el-icon v-else style="margin-right:4px;"><Check /></el-icon>
             {{ syncStatus.universe.text }}
          </div>
        </div>
      </div>
      <div class="panel-section">
        <h3>已授权角色列表</h3>
        <el-scrollbar max-height="500px" always>
          <div v-for="(chars, corpName) in groupedAuthChars" :key="corpName" class="corp-group">
            <div class="corp-header"><el-icon><OfficeBuilding /></el-icon> {{ corpName }}</div>
            <div v-for="char in chars" :key="char.character_id" class="char-item">
              <div class="char-info"><el-avatar :size="24" :src="getOwnerIcon(char.character_id, false)" /> <span>{{ char.character_name }}</span></div>
              <div class="char-actions">
                <el-tag v-if="char.is_director" type="warning" size="small" effect="dark" style="margin-right:5px">总监</el-tag>
                <el-button type="danger" link icon="Delete" size="small" @click="removeChar(char.character_id)"></el-button>
              </div>
            </div>
          </div>
        </el-scrollbar>
      </div>
    </el-drawer>

    <el-dialog v-model="dialogVisible" :title="containerTitle" width="700px" class="dark-dialog" draggable top="8vh">
      <el-table :data="containerItems" style="width: 100%" height="550px" class="asset-table" stripe>
        <el-table-column width="60" align="right">
          <template #default="{row}">
             <div class="icon-col">
               <span v-if="row.quantity > 1" class="qty-modern" style="font-size:13px">{{ row.quantity }}</span>
               <div class="img-box" style="width:32px;height:32px">
                 <el-image :src="`https://image.evepc.163.com/Type/${row.type_id}_32.png`" class="mini-icon"/>
               </div>
             </div>
          </template>
        </el-table-column>
        <el-table-column prop="typeName" label="物品" />
        <el-table-column label="位置" width="120">
           <template #default="{row}">
             <el-tag v-if="row.is_ship_fitted" type="danger" size="small" effect="dark">Fitted</el-tag>
             <el-tag v-else type="info" size="small" effect="dark">Cargo</el-tag>
           </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </el-container>
</template>

<script setup>
import { ref, reactive, onMounted, computed, watch } from 'vue'
import axios from 'axios'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Platform, Menu, Location, Box, Edit, View, OfficeBuilding, ArrowDown, Delete, Loading, Check } from '@element-plus/icons-vue'

const router = useRouter()
const loading = ref(false)
const tableData = ref([])
const total = ref(0)
const totalQuantity = ref(0)
const statistics = ref([])
const ownerOptions = ref([])
const locationStats = ref([])
const categoryOptions = ref([])
const authChars = ref([])
const controlPanelVisible = ref(false)
const syncingAssets = ref(false)
const syncingUniverse = ref(false)
const locationFilterText = ref('')
const authUrlInput = ref('')
const dbMTime = ref('探测中...')

const authLink = "https://login.evepc.163.com/v2/oauth/authorize?response_type=code&redirect_uri=https://ali-esi.evepc.163.com/ui/oauth2-redirect.html&client_id=bc90aa496a404724a93f41b4f4e97761&scope=esi-assets.read_assets.v1%20esi-assets.read_corporation_assets.v1%20esi-characters.read_blueprints.v1%20esi-corporations.read_blueprints.v1%20esi-characters.read_corporation_roles.v1%20esi-location.read_location.v1%20esi-location.read_ship_type.v1%20esi-universe.read_structures.v1&state=mystate&realm=ESI&device_id=eve_asset_tool_v3"

const query = reactive({ q: '', owner_ids: [], category_id: null, location_name: null, include_fitted: false, page: 1, limit: 100 })

const groupedAuthChars = computed(() => {
  const groups = {}
  authChars.value.forEach(char => {
    const corp = char.corp_name || `Corp ID: ${char.corp_id || 'Unknown'}`
    if (!groups[corp]) groups[corp] = []
    groups[corp].push(char)
  })
  return groups
})

const filteredLocations = computed(() => {
  if (!locationFilterText.value) return locationStats.value
  const txt = locationFilterText.value.toLowerCase()
  return locationStats.value.filter(l => l.location_name.toLowerCase().includes(txt))
})

const loadFilters = async () => {
  try {
    const res = await axios.get('/api/filters')
    ownerOptions.value = res.data.owners
    locationStats.value = res.data.locations
    categoryOptions.value = res.data.categories
  } catch (e) { console.error('Load filters failed', e) }
}

const loadAuthChars = async () => { try { const res = await axios.get('/api/auth/list'); authChars.value = res.data } catch(e){} }

const loadData = async () => {
  loading.value = true
  try {
    const params = {
      q: query.q, include_fitted: query.include_fitted, location_name: query.location_name,
      owner_ids: query.owner_ids.join(','), category_id: query.category_id, page: query.page, limit: query.limit
    }
    const res = await axios.get('/api/search', { params })
    tableData.value = res.data.data
    total.value = res.data.total
    totalQuantity.value = res.data.total_quantity
    statistics.value = res.data.statistics
  } catch (e) { ElMessage.error('加载失败') }
  finally { loading.value = false }
}

const handleSearch = () => { query.page = 1; loadData() }
const selectLocation = (name) => { query.location_name = name; handleSearch() }

const syncStatus = reactive({
  assets: { running: false, text: "正在待命" },
  universe: { running: false, text: "正在待命" }
})
let syncTimer = null

const pollSyncStatus = async () => {
  if (!controlPanelVisible.value) return;
  try {
    const res = await axios.get('/api/sync/status')
    if (res.data.db_mtime) dbMTime.value = res.data.db_mtime
    
    // 监听资产同步完成或失败事件
    if (syncStatus.assets.running && !res.data.assets.running) {
      if (res.data.assets.has_error) {
        ElNotification({ title: '拉取异常', message: '疑似官方接口暂未开启，请稍后重试', type: 'error', duration: 10000 })
      } else if (res.data.assets.text.includes('完成')) {
        setTimeout(() => { window.location.reload() }, 1500)
      }
    }
    
    syncStatus.assets = res.data.assets
    syncStatus.universe = res.data.universe
  } catch (e) {}
}

watch(controlPanelVisible, (newVal) => {
  if (newVal) {
    pollSyncStatus()
    syncTimer = setInterval(pollSyncStatus, 1000)
  } else {
    clearInterval(syncTimer)
  }
})

const syncTask = async (type) => {
  try {
    await axios.post(`/api/sync/${type}`)
    ElMessage.success('后端系统线程已指派启动。')
    pollSyncStatus()
  } catch(e) { ElMessage.error(e.response?.data?.detail || '启动失败，系统锁已被占用') }
}

const submitAuth = async () => {
  if (!authUrlInput.value) return
  try {
    const res = await axios.post('/api/auth/add', { url: authUrlInput.value })
    ElMessage.success(res.data.message); 
    authUrlInput.value = ''; 
    loadAuthChars()
    // 自动跟随拉取资产
    syncTask('assets')
  } catch(e) { ElMessage.error(e.response?.data?.detail || '授权失败') }
}

const removeChar = async (id) => {
  ElMessageBox.confirm('确定要移除该角色授权吗？', '提示', { type: 'warning' }).then(async () => {
    try {
      await axios.delete(`/api/auth/remove/${id}`)
      ElMessage.success('已移除')
      loadAuthChars()
    } catch(e) { ElMessage.error('操作失败') }
  })
}

const clearLoginCache = () => { window.open("https://login.evepc.163.com/account/logoff", "_blank") }

const dialogVisible = ref(false)
const containerTitle = ref('')
const containerItems = ref([])
const openContainer = async (row) => {
  containerTitle.value = `内部: ${row.custom_name || row.typeName}`
  dialogVisible.value = true
  try { const res = await axios.get('/api/search', { params: { root_id: row.item_id, limit: 1000 } }); containerItems.value = res.data.data } catch(e) {}
}

const formatQty = (n) => n > 9999 ? (n/1000).toFixed(1)+'k' : n

const canOpen = (row) => {
  if (!row.is_singleton) return false
  if (row.categoryID === 6) return true
  if ([12, 340, 448, 649].includes(row.groupID)) return true
  return false
}

const getOwnerIcon = (id, is_corp) => `https://image.evepc.163.com/${is_corp?'Corporation':'Character'}/${id}_64.${is_corp?'png':'jpg'}`

const checkUniversePrompt = () => {
  if (!sessionStorage.getItem('universePrompted')) {
    sessionStorage.setItem('universePrompted', '1');
    setTimeout(() => {
      ElMessageBox.confirm(
        '检测到刚启动系统框架，是否立刻连网检查并更新 EVE 最新的宇宙物品数据库？\n(这不仅将补全部分初装状态缺少的物品图片和中文名，还能保障最新实装版本新船及组件均可被正确识别)',
        '更新物品名录建议',
        { confirmButtonText: '立刻网络更新', cancelButtonText: '目前不需要', type: 'info' }
      ).then(() => {
        syncTask('universe');
      }).catch(() => {});
    }, 500);
  }
}

onMounted(() => { 
  document.documentElement.classList.add('dark'); 
  loadFilters(); 
  loadData(); 
  loadAuthChars();
  
  if (!localStorage.getItem('first_time_tutorial')) {
    localStorage.setItem('first_time_tutorial', '1');
    let countdown = 8;
    ElMessageBox.alert(
      `<div style="line-height: 1.8; font-size: 14px;">
        <p>1. 请点击右上角<b>控制台</b>按钮添加所需管理资产角色的 ESI 授权。如该角色为军团总监，则将同时自动同步军团机库资产。</p>
        <p>2. <strong style="color: #f56c6c; font-size: 16px;">务必妥善保管</strong>本程序同级目录下生成的 <b>data</b> 数据库文件夹，内包含您的 ESI 授权 Token 以及所有资产信息，<strong style="color: #f56c6c; font-size: 16px;">请勿随意与他人分享</strong>。</p>
        <div style="margin-top: 15px; padding: 10px; background: rgba(230, 162, 60, 0.1); border-left: 4px solid #e6a23c; color: #e6a23c; font-size: 13px;">
          <b style="font-size: 14px;">关于国服特有缓存与维护机制：</b><br>
          官方 ESI 资产接口针对游戏内的新操作存在约<b>一小时</b>的防刷缓存限制期。如果您刚才整理过机库但在这刷新没变化，请一小时后再做同步。<br><br>
          同时，近期疑似网易官方会在<b>每天早上 9:00 - 12:00 左右掐断所有人的拉取权限</b>进行每日维护（期间拉取将被判作为 0 资产直接失败）。请在其他时间同步。(详情查看:https://ali-esi.evepc.163.com/ui/?version=latest#/)
        </div>
      </div>`,
      '欢迎使用 EVE 资产管理平台',
      {
        dangerouslyUseHTMLString: true,
        confirmButtonText: `我已了解 (${countdown}s)`,
        type: 'warning',
        showClose: false,
        closeOnClickModal: false,
        closeOnPressEscape: false,
        confirmButtonClass: 'countdown-disabled',
        beforeClose: (action, instance, done) => {
          if (countdown <= 0) done();
        },
        callback: () => {
          checkUniversePrompt();
        }
      }
    )
    const countTimer = setInterval(() => {
      countdown--;
      const btn = document.querySelector('.el-message-box__btns .el-button--primary');
      if (btn) {
        if (countdown > 0) {
          btn.innerHTML = `<span>我已了解 (${countdown}s)</span>`;
        } else {
          btn.innerHTML = `<span>我已了解</span>`;
          btn.classList.remove('countdown-disabled');
          clearInterval(countTimer);
        }
      }
    }, 1000);
  } else {
    checkUniversePrompt();
  }
})
</script>

<style scoped>
.layout-container { height: 100vh; background-color: #0b0c0e; color: #cfd3dc; font-family: "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
.header { background-color: #151618; border-bottom: 1px solid #2a2b2d; display: flex; align-items: center; justify-content: space-between; padding: 0 20px; height: 70px; z-index: 10; }
.logo { font-size: 18px; font-weight: bold; color: #409eff; display: flex; align-items: center; gap:10px; min-width: 200px;}
.author-credits { font-size: 10px; color: #606266; font-weight: normal; margin-top: 4px; padding-left: 5px; opacity: 0.8; }
.top-search-area { display: flex; align-items: center; gap: 12px; flex: 1; margin: 0 30px; justify-content: center; } 
.filter-group { display: flex; gap: 10px; }
.filter-item { width: 140px; }
.main-search-input { width: 400px; }
.search-checkbox { color: #aaa; background: #1f2022; border-color: #333; }

.sidebar { background-color: #111214; border-right: 1px solid #2a2b2d; display: flex; flex-direction: column; margin-right: 2px; }
.sidebar-header { padding: 15px; border-bottom: 1px solid #222; }
.sidebar-input :deep(.el-input__wrapper) { background-color: #1a1b1d; box-shadow: none; border: 1px solid #333; }
.location-list { list-style: none; padding: 0; margin: 0; }
.location-item { padding: 10px 15px; cursor: pointer; display: flex; align-items: center; justify-content: space-between; color: #bbb; font-size: 13px; border-left: 3px solid transparent; transition: all 0.2s; }
.location-item:hover { background-color: #1a1b1d; color: #fff; }
.location-item.active { background-color: #202124; border-left-color: #409eff; color: #409eff; }
.loc-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px; }

:global(.countdown-disabled) { pointer-events: none !important; opacity: 0.6 !important; filter: grayscale(100%); cursor: not-allowed !important; }
.loc-count { background: #2a2a2a; color: #777; padding: 2px 6px; border-radius: 4px; font-size: 11px; }

.main-content { padding: 0 0 0 10px; background-color: #0b0c0e; display: flex; flex-direction: column; }
.asset-table { width: 100%; }

.stat-group { display: flex; align-items: center; gap: 20px; color: #888; font-size: 13px; }
.stat-item { display: flex; align-items: center; gap: 6px; }
.stat-item.pointer { cursor: pointer; transition: color 0.2s; }
.stat-item.pointer:hover { color: #fff; }
.highlight { color: #409eff; font-weight: bold; font-size: 14px; margin-left: 2px; font-family: 'Helvetica Neue', sans-serif; }
.more-icon { font-size: 10px; margin-left: 2px; }

.stat-list { max-height: 400px; overflow-y: auto; padding: 5px; }
.stat-title { font-weight: bold; color: #fff; border-bottom: 1px solid #333; padding-bottom: 8px; margin-bottom: 8px; }
.stat-row { display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px dashed #2a2a2a; font-size: 13px; }
.stat-name { color: #ccc; }
.stat-val { color: #409eff; font-family: monospace; font-weight: bold; }

.icon-col { display: flex; align-items: center; gap: 12px; justify-content: flex-end; padding-right: 12px; }
.qty-modern { font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 15px; color: #fff; font-weight: 700; text-shadow: 0 1px 2px rgba(0,0,0,0.5); }
.img-box { position: relative; width: 42px; height: 42px; flex-shrink: 0; }
.img-box .el-image { width: 100%; height: 100%; border-radius: 4px; border: 1px solid #333; }
.bp-tag { position: absolute; bottom: 0; right: 0; background: #007bff; color: white; font-size: 9px; padding: 0 2px; }

.detail-col { display: flex; flex-direction: column; justify-content: center; height: 100%; gap: 4px; }
.primary-row { display: flex; align-items: baseline; gap: 8px; }
.item-name { font-size: 16px; color: #e0e0e0; font-weight: 500; font-family: "Microsoft YaHei", sans-serif; }
.custom-highlight { color: #f2c037; font-weight: bold; font-size: 17px; }
.real-name { color: #777; font-size: 13px; font-family: "Segoe UI", sans-serif; }
.meta-row { display: flex; gap: 6px; align-items: center; }
.cat-tag { background-color: #1f2225; border-color: #333; color: #888; font-size: 11px; height: 18px; line-height: 16px; padding: 0 4px; }

.location-col { display: flex; flex-direction: column; justify-content: center; gap: 4px; }
.loc-text { font-size: 14px; color: #ccc; }
.container-hint { display: inline-flex; align-items: center; gap: 6px; color: #409eff; font-size: 12px; background: rgba(64, 158, 255, 0.08); padding: 2px 6px; border-radius: 4px; align-self: flex-start; border: 1px solid rgba(64, 158, 255, 0.2); }

.owner-col { display: flex; align-items: center; gap: 12px; }
.owner-text { font-size: 13px; color: #ddd; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.footer-bar { height: 40px; background: #151618; border-top: 1px solid #2a2a2a; display: flex; align-items: center; justify-content: flex-end; padding: 0 20px; }
.status-text { color: #666; font-size: 12px; }

/* 侧边栏面板样式 */
.panel-section { 
  margin-bottom: 20px; 
  background-color: #25282c;
  border: 1px solid #36393f; 
  border-radius: 8px; 
  padding: 15px;
  box-shadow: 0 4px 6px rgba(0, 0, 0, 0.2);
}

.panel-section h3 { 
  margin-top: 0; 
  margin-bottom: 15px;
  color: #66b1ff; 
  font-size: 15px; 
  font-weight: bold;
  border-bottom: 1px solid #3e4147;
  padding-bottom: 8px;
  display: flex;
  align-items: center;
}
.sync-status-row { display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; }
.status-monitor { background: #151618; color: #555; font-size: 11px; padding: 5px 8px; border-radius: 4px; font-family: 'Consolas', 'Courier New', Courier, monospace; border: 1px dashed #2a2a2a; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; display: flex; align-items: center; transition: all 0.3s ease; }
.status-monitor.active { color: #67c23a; border: 1px solid #336b1d; box-shadow: 0 0 5px rgba(103, 194, 58, 0.2); }
.hint { font-size: 12px; color: #888; margin-bottom: 5px; }
.mini-icon { width: 24px; height: 24px; vertical-align: middle; margin-left: 5px; border-radius: 2px;}

.corp-group { margin-bottom: 15px; }
.corp-header { color: #888; font-size: 12px; font-weight: bold; margin-bottom: 5px; border-bottom: 1px solid #333; padding-bottom: 2px; display: flex; align-items: center; gap: 5px; }
.char-item { display: flex; justify-content: space-between; align-items: center; padding: 5px 0; }
.char-info { display: flex; align-items: center; gap: 8px; color: #ddd; font-size: 13px; }
.char-actions { display: flex; align-items: center; gap: 5px; }

/* 修改点：高级感纯紫色按钮样式 */
.console-btn {
  background-color: #626aef; /* Indigo/Purple 纯色 */
  border-color: #626aef;
  color: #ffffff;
  font-weight: 600;
  transition: all 0.3s;
}

.console-btn:hover, .console-btn:focus {
  background-color: #7c82f2; /* 悬浮时稍微变亮 */
  border-color: #7c82f2;
  color: #ffffff;
  box-shadow: 0 0 8px rgba(98, 106, 239, 0.6); /* 增加紫色微光 */
}

/* CSS Overrides for Spacer Columns */
.spacer-col { pointer-events: none; border-bottom: 1px solid #222 !important; }

:deep(.el-table) { --el-table-bg-color: #0b0c0e; --el-table-tr-bg-color: #0b0c0e; --el-table-header-bg-color: #151618; --el-table-border-color: #222; --el-table-text-color: #ccc; --el-table-header-text-color: #888; --el-table-row-hover-bg-color: #1a1b1d; }
:deep(.el-input__wrapper), :deep(.el-textarea__inner) { background-color: #1a1b1d !important; box-shadow: 0 0 0 1px #333 inset !important; color: white !important; }
:deep(.el-input__inner) { color: #fff; }
:deep(.el-select-dropdown__item.hover) { background-color: #333; }

:deep(.el-drawer) { 
  background-color: #1e2024 !important; 
  color: #e0e0e0;
}
:deep(.el-drawer__header) {
  margin-bottom: 0;
  padding: 20px;
  border-bottom: 1px solid #2f3237;
  background-color: #1b1d21;
  color: #fff;
}
:deep(.el-drawer__title) {
  font-weight: bold;
  font-size: 18px;
  color: #fff !important;
}
:deep(.el-dialog) { background-color: #1a1b1d; }
:deep(.el-dialog__title) { color: #fff; }
:global(.dark-popover) { background-color: #1a1b1d !important; border-color: #333 !important; color: #fff !important; }
:global(.el-popper__arrow::before) { background-color: #1a1b1d !important; border-color: #333 !important; }
</style>