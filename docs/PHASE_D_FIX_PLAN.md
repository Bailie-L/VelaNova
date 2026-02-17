# VelaNova Phase D Fix Plan - Memory System Optimization

**Date:** 2025-10-10 (Africa/Johannesburg)
**Status:** PLANNING
**Target:** Phase D Re-Acceptance
**Mode:** Offline

---

## Executive Summary

Phase D memory system has 5 confirmed issues preventing full acceptance. This plan details fixes in priority order with implementation steps, testing procedures, and acceptance criteria.

**Current State:** PARTIALLY FUNCTIONAL
**Target State:** FULLY OPERATIONAL + OPTIMIZED
**Estimated Responses:** 20-25 (within budget of 30)

---

## Issues Summary

| ID | Issue | Priority | Impact | Responses |
|----|-------|----------|--------|-----------|
| D1 | Session Persistence Broken | HIGH | Memory fragmentation | 4 |
| D2 | Semantic Threshold Too High | MEDIUM | Context exclusion | 3 |
| D3 | Configuration Schema Missing | MEDIUM | Non-configurable | 3 |
| D4 | Documentation Outdated | HIGH | Maintenance burden | 2 |
| D5 | Logging Insufficient | LOW | Debug difficulty | 2 |

**Total Implementation:** 14 responses
**Testing & Validation:** 8 responses
**Documentation & Handover:** 4 responses
**Buffer:** 4 responses

---

## Fix 1: Session Persistence (HIGH PRIORITY)

### Current State
```python
# Line 1189 - voice_loop.py
self.state = ConversationState(
    session_id=f"session_{int(time.time())}"  # Always creates new session
)
Problem

New session on every restart
55 sessions for 119 messages (2.16 avg)
"Recall across restart" (D3) failing
Memory fragmented

Solution Design
Step 1.1: Add Session Resume Method to MemoryStore
pythondef get_latest_session(self, max_age_hours: int = 24) -> Optional[str]:
    """Get most recent session if within age limit."""
    if not self.enabled:
        return None
    
    try:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT session_id, MAX(timestamp) as last_activity
            FROM conversations
            GROUP BY session_id
            ORDER BY last_activity DESC
            LIMIT 1
        """)
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            session_id, last_activity = row
            last_time = datetime.fromisoformat(last_activity)
            age_hours = (datetime.now() - last_time).total_seconds() / 3600
            
            if age_hours <= max_age_hours:
                return session_id
        
        return None
    except Exception as e:
        self.logger.error("get_latest_session_failed %s", json.dumps({"error": str(e)}))
        return None
Step 1.2: Add Session Resume to VoiceLoop.init
python# Around line 1189 - voice_loop.py
# Determine session ID (resume or create)
session_timeout_hours = self.cfg.get("memory", {}).get("session_timeout_hours", 24)
resumed_session = self.memory.get_latest_session(session_timeout_hours)

if resumed_session:
    session_id = resumed_session
    self.logger.info("session_resumed %s", json.dumps({
        "session_id": session_id,
        "timeout_hours": session_timeout_hours
    }))
else:
    session_id = f"session_{int(time.time())}"
    self.logger.info("session_created %s", json.dumps({
        "session_id": session_id
    }))

self.state = ConversationState(session_id=session_id)
Step 1.3: Add Configuration
yaml# config/voice.yaml - memory section
memory:
  enabled: true
  max_history: 100
  embedding_model: all-MiniLM-L6-v2
  session_timeout_hours: 24      # NEW: Resume if < 24h old
  session_resume_enabled: true   # NEW: Enable/disable resume
Step 1.4: Add Session Info Method
pythondef get_session_info(self, session_id: str) -> Dict[str, Any]:
    """Get session metadata."""
    if not self.enabled:
        return {}
    
    try:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT 
                COUNT(*) as turn_count,
                MIN(timestamp) as started,
                MAX(timestamp) as last_activity
            FROM conversations
            WHERE session_id = ?
        """, (session_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                "turn_count": row[0],
                "started": row[1],
                "last_activity": row[2]
            }
        return {}
    except Exception as e:
        self.logger.error("get_session_info_failed %s", json.dumps({"error": str(e)}))
        return {}
Testing Plan
Test 1.1: Session Resume After Restart
bash# Run 1: Create identifiable conversation
python3 voice_loop.py
> "Remember: test codeword is BANANA-42"
> exit

# Run 2: Verify session resumed
python3 voice_loop.py
# Check logs for "session_resumed"
grep "session_resumed" logs/voice_loop-*.log | tail -1

# Run 3: Verify recall works
> "What was the test codeword?"
# Expected: Response mentions BANANA-42
Test 1.2: Session Timeout Creates New
bash# Manually age last session beyond 24h
sqlite3 data/memory.db "UPDATE conversations SET timestamp = datetime('now', '-25 hours') WHERE session_id = (SELECT session_id FROM conversations ORDER BY id DESC LIMIT 1);"

# Restart should create new session
python3 voice_loop.py
grep "session_created" logs/voice_loop-*.log | tail -1
Test 1.3: Session Statistics
bash# Verify session consolidation
sqlite3 data/memory.db "SELECT COUNT(DISTINCT session_id) as sessions, COUNT(*) as messages, CAST(COUNT(*) AS FLOAT) / COUNT(DISTINCT session_id) as avg_per_session FROM conversations;"

# Expected: Fewer sessions, higher avg_per_session
Acceptance Criteria

✅ Session resumes within timeout window
✅ New session created after timeout
✅ Logs show "session_resumed" or "session_created"
✅ Conversation recall works across restart
✅ Avg messages per session increases (target: >10)

Implementation Steps

Response 4: Add get_latest_session() to MemoryStore
Response 5: Add get_session_info() to MemoryStore
Response 6: Update VoiceLoop.init() with resume logic
Response 7: Update config/voice.yaml with session parameters


Fix 2: Semantic Search Threshold (MEDIUM PRIORITY)
Current State
python# Line 1371 - voice_loop.py
for content, score in semantic_hits:
    if score > 0.7:  # Hardcoded threshold
        parts.append(f"- {content[:200]}")
Problem

Threshold 0.7 too high
"What time is it?" scores 0.6973 (excluded)
No configurability
No visibility into excluded results

Solution Design
Step 2.1: Make Threshold Configurable
python# MemoryStore.__init__ - around line 250
self.semantic_threshold = self.cfg.get("semantic_threshold", 0.65)
self.logger.info("memory_config %s", json.dumps({
    "enabled": self.enabled,
    "max_history": self.max_history,
    "embedding_model": self.cfg.get("embedding_model"),
    "semantic_threshold": self.semantic_threshold
}))
Step 2.2: Update search_semantic() with Logging
pythondef search_semantic(self, query: str, limit: int = 3) -> List[Tuple[str, float]]:
    """Semantic search using embeddings."""
    if not self.enabled or not self.embedder:
        return []

    try:
        query_emb = self.embedder.encode(query)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT content, embedding FROM conversations
            WHERE embedding IS NOT NULL
            ORDER BY id DESC
            LIMIT 100
        """)

        results = []
        excluded = []
        for content, emb_bytes in cursor:
            if emb_bytes:
                try:
                    emb = np.frombuffer(emb_bytes, dtype=np.float32)
                    score = np.dot(query_emb, emb) / (np.linalg.norm(query_emb) * np.linalg.norm(emb))
                    
                    if float(score) >= self.semantic_threshold:
                        results.append((content, float(score)))
                    else:
                        # Track near-misses (within 0.05 of threshold)
                        if float(score) >= self.semantic_threshold - 0.05:
                            excluded.append((content[:60], float(score)))
                except Exception:
                    continue

        conn.close()

        # Log results and near-misses
        results.sort(key=lambda x: x[1], reverse=True)
        top_results = results[:limit]
        
        self.logger.info("semantic_search %s", json.dumps({
            "query_chars": len(query),
            "total_scored": len(results) + len(excluded),
            "above_threshold": len(results),
            "returned": len(top_results),
            "excluded_near_miss": len(excluded)
        }))
        
        # Log top results with scores
        for i, (content, score) in enumerate(top_results):
            self.logger.debug("semantic_hit %s", json.dumps({
                "rank": i + 1,
                "score": round(score, 4),
                "content": content[:80]
            }))
        
        # Log excluded near-misses
        for content_preview, score in excluded:
            self.logger.debug("semantic_excluded %s", json.dumps({
                "score": round(score, 4),
                "threshold": self.semantic_threshold,
                "content": content_preview
            }))

        return top_results
    except Exception as e:
        self.logger.error("semantic_search_failed %s", json.dumps({"error": str(e)}))
        return []
Step 2.3: Update _prepare_context() with Threshold
python# Line 1364-1372 - voice_loop.py
# Semantic search
semantic_hits = self.memory.search_semantic(user_text, limit=2)
if semantic_hits:
    parts.append("Relevant context:")
    for content, score in semantic_hits:
        # Threshold now applied in search_semantic(), just use results
        parts.append(f"- {content[:200]}")
Step 2.4: Add Configuration
yaml# config/voice.yaml - memory section
memory:
  enabled: true
  max_history: 100
  embedding_model: all-MiniLM-L6-v2
  session_timeout_hours: 24
  session_resume_enabled: true
  semantic_threshold: 0.65        # NEW: Configurable relevance threshold
Testing Plan
Test 2.1: Threshold Effectiveness
python# Test script: test_semantic_threshold.py
from voice_loop import MemoryStore, load_config, ensure_logger
from pathlib import Path

cfg = load_config()
logger, _ = ensure_logger(cfg.get('logging', {}))
mem = MemoryStore(Path('data/memory.db'), logger, cfg)

queries = [
    "Tell me the current time",
    "What time is it?",
    "Time check please",
    "What's the date today?"
]

print(f"Semantic Threshold: {mem.semantic_threshold}")
print("-" * 80)

for query in queries:
    results = mem.search_semantic(query, limit=5)
    print(f"\nQuery: {query}")
    for content, score in results:
        print(f"  Score: {score:.4f} | Content: {content[:60]}")
    if not results:
        print("  (no results above threshold)")
Test 2.2: Log Visibility
bash# Run system and check logs for semantic search details
python3 voice_loop.py
> "What time is it?"

# Check logs for semantic_search, semantic_hit, semantic_excluded
grep "semantic_" logs/voice_loop-*.log | tail -10
Test 2.3: Configuration Override
bash# Test with different threshold
sed -i 's/semantic_threshold: 0.65/semantic_threshold: 0.60/' config/voice.yaml
python3 voice_loop.py
# Verify threshold logged in "memory_config"
grep "memory_config" logs/voice_loop-*.log | tail -1
Acceptance Criteria

✅ Threshold configurable in voice.yaml
✅ Threshold logged at startup
✅ Paraphrased queries return results (score 0.65-0.70)
✅ Near-misses logged for tuning
✅ Semantic search effectiveness improved

Implementation Steps

Response 8: Update MemoryStore.init() with threshold config
Response 9: Update search_semantic() with enhanced logging
Response 10: Update _prepare_context() to remove duplicate threshold


Fix 3: Configuration Schema (MEDIUM PRIORITY)
Current State
yaml# config/voice.yaml - memory section (current)
memory:
  enabled: true
  max_history: 100
  embedding_model: all-MiniLM-L6-v2
Problem

No session management parameters
No semantic threshold
No cleanup configuration
Non-discoverable settings

Solution Design
Step 3.1: Extended Memory Configuration
yaml# config/voice.yaml - memory section (complete)
memory:
  enabled: true
  max_history: 100
  embedding_model: all-MiniLM-L6-v2
  
  # Session Management
  session_timeout_hours: 24
  session_resume_enabled: true
  
  # Semantic Search
  semantic_threshold: 0.65
  semantic_search_limit: 3
  
  # Context Management
  max_context_turns: 5
  context_include_semantic: true
  
  # Maintenance (future Phase I)
  cleanup_enabled: false
  cleanup_age_days: 90
  max_sessions: 100
Step 3.2: Configuration Validation
python# Add to MemoryStore.__init__() - around line 250
def _validate_config(self):
    """Validate memory configuration."""
    warnings = []
    
    # Check threshold range
    threshold = self.cfg.get("semantic_threshold", 0.65)
    if threshold < 0.5 or threshold > 0.9:
        warnings.append(f"semantic_threshold {threshold} outside recommended range 0.5-0.9")
    
    # Check session timeout
    timeout = self.cfg.get("session_timeout_hours", 24)
    if timeout < 1 or timeout > 168:  # 1 hour to 1 week
        warnings.append(f"session_timeout_hours {timeout} outside recommended range 1-168")
    
    # Check max history
    max_hist = self.cfg.get("max_history", 100)
    if max_hist < 10 or max_hist > 1000:
        warnings.append(f"max_history {max_hist} outside recommended range 10-1000")
    
    if warnings:
        self.logger.warning("memory_config_warnings %s", json.dumps({"warnings": warnings}))
    
    return len(warnings) == 0
Step 3.3: Default Configuration Generator
pythondef load_config() -> Dict[str, Any]:
    """Load YAML config from the canonical voice.yaml path."""
    if not CONFIG_PATH.exists():
        # Create default config if missing
        default_cfg = {
            # ... existing sections ...
            "memory": {
                "enabled": True,
                "max_history": 100,
                "embedding_model": "all-MiniLM-L6-v2",
                "session_timeout_hours": 24,
                "session_resume_enabled": True,
                "semantic_threshold": 0.65,
                "semantic_search_limit": 3,
                "max_context_turns": 5,
                "context_include_semantic": True,
                "cleanup_enabled": False,
                "cleanup_age_days": 90,
                "max_sessions": 100
            }
        }
        # ... rest of function ...
Testing Plan
Test 3.1: Configuration Loading
bash# Verify all parameters loaded
python3 -c "
from voice_loop import load_config
import json
cfg = load_config()
print(json.dumps(cfg.get('memory', {}), indent=2))
"
Test 3.2: Validation Warnings
bash# Test with invalid threshold
sed -i 's/semantic_threshold: 0.65/semantic_threshold: 0.95/' config/voice.yaml
python3 voice_loop.py
grep "memory_config_warnings" logs/voice_loop-*.log | tail -1

# Restore valid threshold
sed -i 's/semantic_threshold: 0.95/semantic_threshold: 0.65/' config/voice.yaml
Test 3.3: Default Generation
bash# Backup and remove config
mv config/voice.yaml config/voice.yaml.backup
python3 voice_loop.py
# Should generate default config with all memory parameters
grep -A 12 "memory:" config/voice.yaml
# Restore
mv config/voice.yaml.backup config/voice.yaml
Acceptance Criteria

✅ All memory parameters documented in config
✅ Default config generator includes full schema
✅ Configuration validation warns on invalid values
✅ Parameters logged at startup

Implementation Steps

Response 11: Update config/voice.yaml with complete memory schema
Response 12: Add configuration validation to MemoryStore.init()
Response 13: Update load_config() default generator


Fix 4: Documentation Update (HIGH PRIORITY)
Current State

PHASE_D_ACCEPTANCE.md references non-existent functions
Functions documented: ensure_doc(), add_chunk(), build_context(), retrieve_guard()
Functions actual: add_turn(), get_recent_turns(), search_semantic(), search_fts()

Problem

Maintenance burden
Confusion for future work
Inaccurate acceptance criteria

Solution Design
Step 4.1: Rewrite PHASE_D_ACCEPTANCE.md
Create accurate acceptance document based on:

Actual implemented functions
Real test procedures
Current configuration schema
Verified evidence

Step 4.2: Add OPERATIONS.md Memory Section
Document:

Session management
Memory queries
Semantic search tuning
Maintenance procedures

Testing Plan
Test 4.1: Documentation Accuracy
bash# Verify all referenced functions exist
grep -o "def [a-z_]*(" orchestrator/voice_loop.py | sort -u > /tmp/actual_functions.txt
grep -oE "[a-z_]+\(\)" docs/PHASE_D_ACCEPTANCE.md | sort -u > /tmp/documented_functions.txt
comm -13 /tmp/actual_functions.txt /tmp/documented_functions.txt
# Should be empty or match actual implementations
Acceptance Criteria

✅ All function references accurate
✅ Test procedures match actual implementation
✅ Configuration examples correct
✅ Evidence paths valid

Implementation Steps

Response 14: Rewrite PHASE_D_ACCEPTANCE.md
Response 15: Add memory section to OPERATIONS.md


Fix 5: Enhanced Logging (LOW PRIORITY)
Current State

Semantic search scores not logged
No session lifecycle logging
No retrieval effectiveness metrics

Solution Design
Already covered in Fix 2 (semantic search logging).
Additional Logging:
python# Session lifecycle
self.logger.info("session_resumed %s", json.dumps({...}))
self.logger.info("session_created %s", json.dumps({...}))

# Retrieval effectiveness
self.logger.info("context_prepared %s", json.dumps({
    "chars": len(context),
    "has_conv": bool(conv_context),
    "semantic_hits": len(semantic_hits),
    "semantic_scores": [round(s, 3) for _, s in semantic_hits]
}))
Implementation Steps

Covered in Fixes 1 & 2


Testing & Validation (Responses 16-23)
Integration Test Suite
Test Suite 1: Session Persistence (2 responses)

Test 1.1: Resume after short restart
Test 1.2: New session after timeout
Test 1.3: Statistics validation

Test Suite 2: Semantic Search (2 responses)

Test 2.1: Threshold effectiveness
Test 2.2: Log visibility
Test 2.3: Configuration override

Test Suite 3: Configuration (2 responses)

Test 3.1: Loading verification
Test 3.2: Validation warnings
Test 3.3: Default generation

Test Suite 4: End-to-End (2 responses)

Full conversation with restart
Memory recall verification
Performance metrics


Snapshot & Documentation (Responses 24-27)
Response 24: Pre-Fix Baseline Snapshot
Create snapshot of current state before fixes.
Response 25: Implementation Complete Snapshot
Create snapshot after all fixes implemented.
Response 26: Update PHASE_D_ACCEPTANCE.md
Rewrite with accurate information.
Response 27: Update OPERATIONS.md
Add memory operations section.

Final Acceptance (Responses 28-30)
Response 28: Final Validation Test
Comprehensive test of all Phase D criteria.
Response 29: Performance Metrics
Database statistics, session consolidation, semantic effectiveness.
Response 30: Phase D Re-Acceptance Document
Final acceptance with evidence.
Response 31: Technical Handover
Comprehensive handover document.

Success Criteria
Phase D Acceptance Criteria (Re-Validation)
IDCriterionCurrentTargetTestD1Engine present/configuredPASSPASSConfig checkD2Write path workingPASSPASSDB verifyD3Recall across restartFAILPASSRestart testD4Retrieval wiring visiblePARTIALPASSLog checkD5Privacy posturePASSPASSConfig verifyD6Snapshot + ledgerPASSPASSFiles exist
Current: 3/6 PASS, 1/6 FAIL, 2/6 PARTIAL
Target: 6/6 PASS
Performance Targets
MetricCurrentTargetMethodAvg messages/session2.16>10SQL querySemantic threshold0.70.65ConfigRecall success rate0%100%Restart testLog visibilityPartialCompleteLog analysis

Risk Management
Known Risks
Risk 1: Database Schema Changes

Mitigation: Backup before changes
Rollback: Restore from snapshot

Risk 2: Configuration Breaking Changes

Mitigation: Validate before applying
Rollback: Restore voice.yaml from backup

Risk 3: Regression in Phase C Features

Mitigation: Run Phase C validation test
Rollback: Full system snapshot restore

Rollback Plan
bash# If any fix breaks system:
# 1. Stop loop
pkill -9 -f "voice_loop.py"

# 2. Restore code
cp orchestrator/voice_loop.py.backup-YYYYMMDD-HHMMSS orchestrator/voice_loop.py

# 3. Restore config
cp config/voice.yaml.backup config/voice.yaml

# 4. Restore database (if needed)
cp data/memory.db.backup data/memory.db

# 5. Verify health
python3 -c "from voice_loop import load_config; load_config()"

Timeline Estimate
PhaseResponsesDescriptionPlanning1-3Analysis, plan creation, approvalImplementation4-13Code changes, configurationTesting14-23Test suites, validationDocumentation24-27Snapshots, acceptance docsFinalization28-31Final tests, handover
Total: 31 responses (within budget)

Next Steps

Response 4: Implement Fix 1.1 (get_latest_session method)
Response 5: Implement Fix 1.2 (session resume logic)
Response 6: Implement Fix 1.3 (configuration update)
Response 7: Test session persistence
Continue through plan...


Document Control
Version: 1.0
Created: 2025-10-10
Author: Claude (VelaNova Assistant)
Status: APPROVED - READY FOR IMPLEMENTATION

END PHASE D FIX PLAN

