# GetComics URL And Viewer Nav Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `getcomics.org` post URLs download correctly and add minimal visible previous/next chevrons flanking the viewer title.

**Architecture:** Keep the downloader fix at the planning boundary in `comics.py` so only actual mirror URLs go through mirror resolution. Add a compact overlay rail in the viewer route and style it in `global.css` while preserving the existing keyboard and edge-hit-zone navigation.

**Tech Stack:** Python 3, `unittest`, React 18, TypeScript, Vite, global CSS

---

### Task 1: Downloader Regression

**Files:**
- Create: `backend/tests/test_comics_downloader.py`
- Modify: `comics.py`
- Test: `backend/tests/test_comics_downloader.py`

- [ ] **Step 1: Write the failing test**

```python
def test_resolve_download_plan_treats_getcomics_post_urls_as_source_pages() -> None:
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest backend.tests.test_comics_downloader`
Expected: FAIL because the current plan sends `getcomics.org` into unsupported mirror resolution.

- [ ] **Step 3: Write minimal implementation**

```python
elif is_known_mirror_host(source_host) and "getcomics.org" not in source_host:
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest backend.tests.test_comics_downloader`
Expected: PASS

### Task 2: Viewer Title Rail

**Files:**
- Modify: `frontend/src/routes/ViewerPage.tsx`
- Modify: `frontend/src/styles/global.css`
- Test: `npm --prefix frontend run build`

- [ ] **Step 1: Add the visible nav rail**

```tsx
<header className="viewer-titlebar">
  <button ... aria-label="Previous page">‹</button>
  <div className="viewer-titlebar__title">{issue.title}</div>
  <button ... aria-label="Next page">›</button>
</header>
```

- [ ] **Step 2: Style the rail and buttons**

```css
.viewer-titlebar { ... }
.viewer-titlebar__nav { ... }
```

- [ ] **Step 3: Run build to verify it passes**

Run: `npm --prefix frontend run build`
Expected: `vite build` completes successfully.

### Task 3: Final Verification

**Files:**
- Test: `python3 -m unittest backend.tests.test_comics_downloader`
- Test: `python3 -m unittest backend.tests.test_covers`
- Test: `npm --prefix frontend run build`

- [ ] **Step 1: Run targeted backend regression checks**

Run: `python3 -m unittest backend.tests.test_comics_downloader backend.tests.test_covers`
Expected: PASS

- [ ] **Step 2: Run frontend build verification**

Run: `npm --prefix frontend run build`
Expected: PASS
