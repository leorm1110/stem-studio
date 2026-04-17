/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_DEV_PROXY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
