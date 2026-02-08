# tests/test_advanced_coding_skills.py

"""
Tests for the advanced coding skills: bug detection, performance analysis, and pair programming
"""

import pytest
import tempfile
import os
from agent.skills.bug_detector import BugDetector, get_bug_detector
from agent.skills.performance_analyzer import PerformanceAnalyzer, get_performance_analyzer
from agent.skills.pair_programming import PairProgramming, get_pair_programming


class TestBugDetector:
    """Test cases for BugDetector class"""
    
    @pytest.fixture
    def detector(self):
        """Create a BugDetector instance for testing"""
        return BugDetector()
    
    def test_detect_hardcoded_password(self, detector):
        """Test detection of hardcoded passwords"""
        code = '''
def login():
    password = "secret123"
    return authenticate(password)
'''
        result = detector.detect_bugs_in_code(code, language="python")
        
        assert result['total_findings'] >= 1
        assert result['critical'] >= 1
        assert any('password' in f['pattern'].lower() for f in result['findings'])
    
    def test_detect_eval_usage(self, detector):
        """Test detection of eval() usage"""
        code = '''
def dangerous():
    result = eval(user_input)
    return result
'''
        result = detector.detect_bugs_in_code(code, language="python")
        
        assert result['total_findings'] >= 1
        assert result['critical'] >= 1
        assert any('eval' in f['pattern'].lower() for f in result['findings'])
    
    def test_detect_bare_except(self, detector):
        """Test detection of bare except clause"""
        code = '''
try:
    risky_operation()
except:
    pass
'''
        result = detector.detect_bugs_in_code(code, language="python")
        
        assert result['total_findings'] >= 1
        assert any('except' in f['pattern'].lower() for f in result['findings'])
    
    def test_detect_javascript_double_equals(self, detector):
        """Test detection of == instead of === in JavaScript"""
        code = '''
if (x == y) {
    return true;
}
'''
        result = detector.detect_bugs_in_code(code, language="javascript")
        
        assert result['total_findings'] >= 1
        assert any('double_equals' in f['pattern'] for f in result['findings'])
    
    def test_double_equals_ignores_strict_inequality(self, detector):
        """Test that !== is not flagged as double equals"""
        code = '''
if (x !== y) {
    return true;
}
'''
        result = detector.detect_bugs_in_code(code, language="javascript")
        
        # Should not detect !== as double_equals issue
        double_equals_issues = [f for f in result['findings'] if f['pattern'] == 'double_equals']
        assert len(double_equals_issues) == 0
    
    def test_detect_clean_code(self, detector):
        """Test that clean code returns no findings"""
        code = '''
def safe_function(name: str) -> str:
    """A safe function with no issues."""
    return f"Hello, {name}!"
'''
        result = detector.detect_bugs_in_code(code, language="python")
        
        assert result['total_findings'] == 0
    
    def test_format_findings_report(self, detector):
        """Test formatting of findings report"""
        result = {
            'filepath': 'test.py',
            'total_findings': 2,
            'critical': 1,
            'high': 1,
            'medium': 0,
            'low': 0,
            'findings': [
                {
                    'pattern': 'test_pattern',
                    'severity': 'critical',
                    'line': 10,
                    'code': 'bad code',
                    'description': 'Test issue'
                }
            ]
        }
        
        report = detector.format_findings_report(result)
        
        assert 'test.py' in report
        assert 'Total findings: 2' in report
        assert 'Critical: 1' in report
    
    def test_path_validation_rejects_absolute_paths(self, detector):
        """Test that absolute paths are rejected"""
        with pytest.raises(ValueError, match="Absolute paths are not allowed"):
            detector._validate_file_path("/etc/passwd")
    
    def test_path_validation_rejects_traversal(self, detector):
        """Test that directory traversal is rejected"""
        with pytest.raises(ValueError, match="Path escapes repository"):
            detector._validate_file_path("../../../etc/passwd")
    
    def test_path_validation_accepts_valid_paths(self, detector):
        """Test that valid relative paths are accepted"""
        validated = detector._validate_file_path("agent/skills/bug_detector.py")
        assert "agent/skills/bug_detector.py" in validated
        assert os.path.isabs(validated)
    
    def test_proactive_scan_counts_all_files(self, detector):
        """Test that proactive scan counts all files, not just files with issues"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            clean_file = os.path.join(tmpdir, "clean.py")
            with open(clean_file, 'w') as f:
                f.write('def safe(): pass')
            
            buggy_file = os.path.join(tmpdir, "buggy.py")
            with open(buggy_file, 'w') as f:
                f.write('password = "secret123"')
            
            # Mock the detector's repo_path to the temp directory
            original_repo_path = detector.repo_path
            detector.repo_path = tmpdir
            
            try:
                result = detector.proactive_scan_directory(".")
                
                # Should count both files
                assert result['files_scanned'] == 2
                # But only one has issues
                assert len(result['files_with_issues']) == 1
            finally:
                detector.repo_path = original_repo_path
    
    def test_format_proactive_scan_report(self, detector):
        """Test formatting of proactive scan report"""
        result = {
            'directory': 'test_dir',
            'files_scanned': 10,
            'total_findings': 5,
            'critical': 2,
            'high': 1,
            'medium': 2,
            'low': 0,
            'files_with_issues': [
                {
                    'filepath': 'file1.py',
                    'total_findings': 3,
                    'critical': 2,
                    'high': 1
                }
            ]
        }
        
        report = detector.format_proactive_scan_report(result)
        
        assert 'Files scanned: 10' in report
        assert 'Total findings: 5' in report
        assert 'file1.py' in report


class TestPerformanceAnalyzer:
    """Test cases for PerformanceAnalyzer class"""
    
    @pytest.fixture
    def analyzer(self):
        """Create a PerformanceAnalyzer instance for testing"""
        return PerformanceAnalyzer()
    
    def test_analyze_complexity_simple(self, analyzer):
        """Test complexity analysis of simple code"""
        code = '''
def simple():
    return 42
'''
        result = analyzer.analyze_complexity(code, language="python")
        
        assert 'cyclomatic_complexity' in result
        assert 'complexity_rating' in result
        assert result['cyclomatic_complexity'] >= 1
    
    def test_analyze_complexity_nested(self, analyzer):
        """Test complexity analysis of nested code"""
        code = '''
def complex():
    for i in range(10):
        if i > 5:
            for j in range(i):
                if j % 2 == 0:
                    print(j)
'''
        result = analyzer.analyze_complexity(code, language="python")
        
        # Should have higher complexity due to nesting
        assert result['cyclomatic_complexity'] > 5
        assert result['nested_depth'] > 2
    
    def test_detect_nested_loops(self, analyzer):
        """Test detection of nested loops"""
        code = '''
for i in range(n):
    for j in range(m):
        process(i, j)
'''
        result = analyzer.analyze_performance(code, language="python")
        
        assert result['total_issues'] > 0
        assert any('nested_loops' in issue['type'] for issue in result['issues'])
    
    def test_suggest_optimizations(self, analyzer):
        """Test optimization suggestions"""
        code = '''
def search(items, target):
    for item in items:
        if target in other_list:
            return item
'''
        suggestions = analyzer.suggest_optimizations(code, language="python")
        
        # Should suggest using set/dict for lookups
        assert len(suggestions) > 0
    
    def test_generate_optimization_report(self, analyzer):
        """Test generation of optimization report"""
        code = '''
def example():
    result = ""
    for i in range(100):
        result += str(i)
    return result
'''
        report = analyzer.generate_optimization_report(code, language="python", filepath="test.py")
        
        assert 'Performance Analysis Report' in report
        assert 'Complexity Analysis' in report
        assert 'test.py' in report


class TestPairProgramming:
    """Test cases for PairProgramming class"""
    
    @pytest.fixture
    def pp(self):
        """Create a PairProgramming instance for testing"""
        return PairProgramming()
    
    def test_start_session(self, pp):
        """Test starting a pair programming session"""
        result = pp.start_session("test_user", "test task")
        
        assert "Session Started" in result
        assert "test task" in result
        assert "test_user" in pp.sessions.values().__iter__().__next__().user_id
    
    def test_end_session(self, pp):
        """Test ending a session"""
        pp.start_session("test_user", "test task")
        result = pp.end_session("test_user")
        
        assert "Session Ended" in result
        # Session should be removed
        assert len([s for s in pp.sessions.values() if s.user_id == "test_user"]) == 0
    
    def test_add_file_to_session(self, pp):
        """Test adding a file to session"""
        pp.start_session("test_user", "test task")
        result = pp.add_file_to_session("test_user", "test.py")
        
        assert "Added" in result
        assert "test.py" in result
    
    def test_get_session_status(self, pp):
        """Test getting session status"""
        pp.start_session("test_user", "test task")
        result = pp.get_session_status("test_user")
        
        assert "Active Pair Programming Session" in result
        assert "test task" in result
    
    def test_session_timeout(self, pp):
        """Test that sessions timeout properly"""
        # Set a very short timeout for testing
        pp.session_timeout_minutes = 0.001  # ~60ms
        
        pp.start_session("test_user", "test task")
        
        # Wait a bit
        import time
        time.sleep(0.1)
        
        # Session should be inactive now
        session = pp._get_user_session("test_user")
        assert session is None or not session.is_active(pp.session_timeout_minutes)
    
    def test_no_session_returns_message(self, pp):
        """Test that operations without session return appropriate message"""
        result = pp.end_session("nonexistent_user")
        
        assert "don't have an active" in result.lower()


class TestCodingAssistantIntentDetection:
    """Test cases for coding assistant intent detection"""
    
    @pytest.fixture
    def assistant(self):
        """Create a CodingAssistant instance for testing"""
        from agent.skills.coding_assistant import CodingAssistant
        return CodingAssistant()
    
    def test_detect_pair_programming_intent(self, assistant):
        """Test detection of pair programming intent"""
        assert assistant.detect_coding_intent("start pair programming") == "pair_programming"
        assert assistant.detect_coding_intent("begin coding together") == "pair_programming"
        assert assistant.detect_coding_intent("end session") == "pair_programming"
    
    def test_detect_bug_detection_intent(self, assistant):
        """Test detection of bug detection intent"""
        assert assistant.detect_coding_intent("find bugs in file test.py") == "bug_detection"
        assert assistant.detect_coding_intent("check for vulnerabilities") == "bug_detection"
        assert assistant.detect_coding_intent("scan for bugs") == "bug_detection"
    
    def test_detect_performance_analysis_intent(self, assistant):
        """Test detection of performance analysis intent"""
        assert assistant.detect_coding_intent("analyze performance") == "performance_analysis"
        assert assistant.detect_coding_intent("check complexity") == "performance_analysis"
        assert assistant.detect_coding_intent("optimize my code") == "performance_analysis"
    
    def test_detect_code_generation_intent(self, assistant):
        """Test detection of code generation intent"""
        assert assistant.detect_coding_intent("generate code") == "code_generation"
        assert assistant.detect_coding_intent("create a function") == "code_generation"
        assert assistant.detect_coding_intent("write a class") == "code_generation"
    
    def test_no_coding_intent_returns_none(self, assistant):
        """Test that non-coding messages return None"""
        assert assistant.detect_coding_intent("hello world") is None
        assert assistant.detect_coding_intent("what's the weather?") is None


class TestGlobalGetters:
    """Test global getter functions"""
    
    def test_get_bug_detector(self):
        """Test global bug detector getter"""
        detector1 = get_bug_detector()
        detector2 = get_bug_detector()
        
        assert detector1 is detector2  # Should return same instance
        assert isinstance(detector1, BugDetector)
    
    def test_get_performance_analyzer(self):
        """Test global performance analyzer getter"""
        analyzer1 = get_performance_analyzer()
        analyzer2 = get_performance_analyzer()
        
        assert analyzer1 is analyzer2  # Should return same instance
        assert isinstance(analyzer1, PerformanceAnalyzer)
    
    def test_get_pair_programming(self):
        """Test global pair programming getter"""
        pp1 = get_pair_programming()
        pp2 = get_pair_programming()
        
        assert pp1 is pp2  # Should return same instance
        assert isinstance(pp1, PairProgramming)
