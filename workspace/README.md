# NovaFrame Product Workspace

This directory contains runtime product data rather than application source code.

```text
workspace/
├── 原始商品图/<product>/       source product images and descriptions
├── 配件超市/<accessory>/       source accessory images and descriptions
├── 组合/<product>/<task>/      prompts, curated references, and formal outputs
└── .museforge/runs/           temporary generation candidates (Git ignored)
```

The API uses this directory by default. Set `MUSEFORGE_WORKSPACE_ROOT` to an absolute path when product data should live outside the repository.

Generated shot folders and temporary run files are ignored by Git. Source material and prepared reference manifests are committed here only for the bundled `MF-DEMO-001` example.
