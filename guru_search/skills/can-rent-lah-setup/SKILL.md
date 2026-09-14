# can-rent-lah-setup

Guide the user through setting up opencli and installing the PropertyGuru CLI adapter.

## Prerequisites check

Before anything else, verify the user has the foundational pieces in place.

### 1. opencli installed

Check if `opencli` is in PATH:

```bash
which opencli || npm install -g @jackwener/opencli
```

### 2. opencli doctor — all green

Run `opencli doctor` and confirm all three are green:

- Daemon: running
- Extension: connected
- At least one Chrome profile connected

If the extension is not connected, guide the user to install the [opencli Browser Bridge](https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk) Chrome extension.

### 3. Chrome logged into PropertyGuru

Ask the user to open Chrome, navigate to https://www.propertyguru.com.sg, and log in. PropertyGuru does NOT require login for searching — but having a logged-in session avoids Cloudflare challenges.

### 4. Install the PropertyGuru adapter

Copy the adapter into opencli's user CLI directory:

```bash
mkdir -p ~/.opencli/clis/propertyguru
cp <path-to-can-rent-lah>/cli/propertyguru/search.js ~/.opencli/clis/propertyguru/search.js
```

Verify it registered:

```bash
opencli propertyguru --help
```

Should show the `search` command with its arguments.

### 5. Quick smoke test

```bash
opencli propertyguru search "clementi" --max 3000 --bedrooms 2 --limit 3 -f json
```

If this returns JSON listings, everything is set up correctly.

## Once everything is green

Confirm: "Can rent lah! Try `/can-rent-lah-search` to start hunting."
