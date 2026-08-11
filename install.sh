#!/usr/bin/env bash

set -e

echo "========================================="
echo "   Agentic Job Finder Setup Script"
echo "========================================="
echo ""

# 1. Ask for directory and clone
DEFAULT_DIR="$HOME/agentic-job-finder"
read -p "Where would you like to install the project? [$DEFAULT_DIR]: " INSTALL_DIR
INSTALL_DIR=${INSTALL_DIR:-$DEFAULT_DIR}

if [ -d "$INSTALL_DIR" ]; then
    echo "Directory $INSTALL_DIR already exists."
    read -p "Do you want to continue anyway? (y/n): " cont
    if [[ "$cont" != "y" ]]; then
        echo "Aborting."
        exit 1
    fi
else
    git clone https://github.com/yoavdim/agentic-job-finder.git "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# 2. Check PyQt5
echo ""
echo "Checking PyQt5 dependency..."
if python3 -c "import PyQt5" 2>/dev/null; then
    echo "PyQt5 is installed."
else
    echo "PyQt5 not found. Attempting to install via pip3..."
    pip3 install PyQt5 || echo "Warning: pip install failed. You may need to install PyQt5 manually."
fi

# 3. Bootstrap data files
echo ""
echo "Creating tracker data files..."
python3 .kiro/scripts/ensure_data_files.py --apply

# 4. Bootstrap job-search-prefs.md if missing
PREFS_FILE=".kiro/steering/job-search-prefs.md"
if [ ! -f "$PREFS_FILE" ]; then
    echo "Creating template job-search-prefs.md..."
    cat << 'EOF' > "$PREFS_FILE"
# Job Search Preferences

**Target Roles:**
- ...

**Location / Commute:**
- ...

**Experience Level:**
- ...

**Must Haves:**
- ...

**Dealbreakers:**
- ...
EOF
fi

# 5. Tab Share setup
echo ""
echo "========================================="
echo "       Tab Share Extension Setup"
echo "========================================="
echo "The Tab Share extension allows the app to preview jobs in a side-by-side Chrome tab."
echo "Please make sure Google Chrome or Chromium is currently open."
read -p "Press Enter when ready to check Tab Share connection..."

if curl -s http://localhost:8766/tabs > /dev/null; then
    echo "Tab Share is already installed and running!"
else
    echo "Tab Share is not running. Let's install it."
    
    read -p "The Job Search app requires Tab Share on Chrome. Do you also want to install it for Firefox? (y/N): " want_ff
    if [[ "$want_ff" =~ ^[Yy]$ ]]; then
        TS_TARGET="both"
    else
        TS_TARGET="chrome"
    fi

    # Delegate entirely to Tab Share's own one-liner installer.
    bash -c "$(curl -fsSL https://raw.githubusercontent.com/yoavdim/tab-share/master/install.sh)" -- "$TS_TARGET"
    cd "$INSTALL_DIR"
fi

echo ""
echo "========================================="
echo "            Setup Complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "1. cd $INSTALL_DIR"
echo "2. Fill out your profile in .kiro/steering/job-search-prefs.md"
echo "3. Launch the UI by running: python3 routine_launcher.py"
echo ""
