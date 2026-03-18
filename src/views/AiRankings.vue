<template>
  <v-container class="ai-rankings">

    <div class="title-block mt-6 mb-2">
      <div class="text-h3">AI & Computer Science Rankings</div>
      <div class="subtitle mt-2">
        Institutions ranked by total citations in the Computer Science field
      </div>
    </div>

    <div class="text-caption text-grey mb-4">
      {{ totalCount ? totalCount.toLocaleString() + ' institutions' : '' }}
      <span v-if="generatedAt"> &middot; last updated {{ generatedAt }}</span>
    </div>

    <v-card flat border rounded>
      <a
        v-for="(institution, index) in institutions"
        :key="institution.id"
        :href="institution.id"
        target="_blank"
        class="ranking-item"
      >
        <div class="rank-number text-h6 text-grey">
          {{ (currentPage - 1) * perPage + index + 1 }}
        </div>
        <div class="institution-info">
          <div class="institution-name">{{ institution.display_name }}</div>
          <div class="institution-meta text-grey">
            <span v-if="institution.type" class="text-capitalize">{{ institution.type }}</span>
            <span v-if="institution.type && institution.country_code"> &middot; </span>
            <span v-if="institution.country_code">{{ institution.country_code }}</span>
          </div>
        </div>
        <div class="stats text-right">
          <div class="text-body-2 font-weight-bold">
            {{ institution.cs_cited_by_count.toLocaleString() }}
          </div>
          <div class="text-caption text-grey">citations</div>
        </div>
      </a>
    </v-card>

    <v-row justify="center" class="my-6" v-if="loading">
      <v-progress-circular indeterminate color="primary" />
    </v-row>

    <v-row justify="center" class="my-6" v-if="!loading && totalCount > 0">
      <v-pagination
        v-model="currentPage"
        :length="numPages"
        :total-visible="7"
        rounded
      />
    </v-row>

  </v-container>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue';
import { useHead } from '@unhead/vue';
import axios from 'axios';

defineOptions({ name: 'AiRankings' });

useHead({ title: 'AI & CS Rankings' });

const allInstitutions = ref([]);
const loading = ref(false);
const currentPage = ref(1);
const perPage = 25;
const generatedAt = ref(null);

const totalCount = computed(() => allInstitutions.value.length);
const numPages = computed(() => Math.ceil(totalCount.value / perPage) || 1);

const institutions = computed(() => {
  const start = (currentPage.value - 1) * perPage;
  return allInstitutions.value.slice(start, start + perPage);
});

async function loadCache() {
  loading.value = true;
  try {
    const response = await axios.get('/ai-rankings-cache.json');
    allInstitutions.value = response.data.results || [];
    if (response.data.generated_at) {
      generatedAt.value = new Date(response.data.generated_at).toLocaleDateString();
    }
  } catch (err) {
    console.error('Failed to load ai-rankings-cache.json:', err);
  } finally {
    loading.value = false;
  }
}

watch(currentPage, () => {
  window.scrollTo({ top: 0, behavior: 'smooth' });
});

onMounted(loadCache);
</script>

<style scoped>
.ai-rankings {
  width: 900px;
  max-width: 95%;
  margin: auto;
}
.subtitle {
  font-size: 15px;
  color: #666;
}
.ranking-item {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 16px;
  text-decoration: none;
  color: inherit;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}
.ranking-item:last-child {
  border-bottom: none;
}
.ranking-item:hover {
  background: rgba(0, 0, 0, 0.03);
}
.rank-number {
  width: 40px;
  text-align: right;
  flex-shrink: 0;
  color: #aaa;
}
.institution-info {
  flex: 1;
  min-width: 0;
}
.institution-name {
  font-size: 16px;
}
.institution-meta {
  font-size: 13px;
  margin-top: 2px;
}
.stats {
  flex-shrink: 0;
  min-width: 80px;
}
</style>
