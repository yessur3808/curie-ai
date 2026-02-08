# agent/skills/bug_detector.py

"""
Bug Detection Skill - Static analysis and proactive bug finding
Analyzes code for common bugs, anti-patterns, and potential issues
"""

import os
import re
import logging
from typing import List, Dict, Optional
from datetime import datetime

# Import llm.manager conditionally
try:
    import llm.manager
    _llm_available = True
except ImportError:
    _llm_available = False

logger = logging.getLogger(__name__)


class BugPattern:
    """Represents a bug pattern to check for"""
    
    def __init__(self, name: str, pattern: str, severity: str, description: str, language: str = None):
        self.name = name
        self.pattern = re.compile(pattern, re.MULTILINE | re.IGNORECASE)
        self.severity = severity  # 'critical', 'high', 'medium', 'low'
        self.description = description
        self.language = language  # None means all languages
    
    def check(self, code: str, language: str = None) -> List[Dict]:
        """Check code for this pattern"""
        if self.language and language and self.language != language:
            return []
        
        findings = []
        for match in self.pattern.finditer(code):
            line_num = code[:match.start()].count('\n') + 1
            findings.append({
                'pattern': self.name,
                'severity': self.severity,
                'line': line_num,
                'code': match.group(0),
                'description': self.description
            })
        return findings


class BugDetector:
    """
    Bug Detection and Analysis System
    Provides static analysis, pattern matching, and AI-powered bug detection
    """
    
    def __init__(self):
        self.patterns = self._initialize_patterns()
        self.model_name = os.getenv("CODING_MODEL_NAME")
        if not self.model_name:
            try:
                from agent.skills.coder import get_coding_model_name
                self.model_name = get_coding_model_name()
            except ImportError:
                self.model_name = None
        # Set repo path for path validation
        self.repo_path = os.getcwd()
    
    def _validate_file_path(self, file_path: str) -> str:
        """
        Validate that file_path is safe and within the repository
        
        Args:
            file_path: Path to validate (relative or absolute)
            
        Returns:
            Validated absolute path
            
        Raises:
            ValueError: If path is absolute or escapes repository directory
        """
        # Reject absolute paths
        if os.path.isabs(file_path):
            raise ValueError(f"Absolute paths are not allowed: {file_path}")
        
        # Join and resolve to absolute path
        full_path = os.path.abspath(os.path.join(self.repo_path, file_path))
        repo_path_abs = os.path.abspath(self.repo_path)
        
        # Check that resolved path is within repo
        if not full_path.startswith(repo_path_abs + os.sep) and full_path != repo_path_abs:
            raise ValueError(f"Path escapes repository: {file_path}")
        
        return full_path
    
    def _initialize_patterns(self) -> List[BugPattern]:
        """Initialize common bug patterns"""
        return [
            # Python patterns
            BugPattern(
                "bare_except",
                r"except\s*:",
                "high",
                "Bare except clause catches all exceptions including system exits",
                "python"
            ),
            BugPattern(
                "eval_usage",
                r"\beval\s*\(",
                "critical",
                "Use of eval() can lead to arbitrary code execution",
                "python"
            ),
            BugPattern(
                "sql_injection",
                r"execute\s*\(\s*['\"].*['\"]\s*(?:%|\.\s*format\s*\()",
                "critical",
                "Potential SQL injection vulnerability - avoid string formatting in SQL; use parameterized queries",
                "python"
            ),
            BugPattern(
                "hardcoded_password",
                r"(password|passwd|pwd)\s*=\s*['\"][^'\"]+['\"]",
                "critical",
                "Hardcoded password detected - use environment variables or secure storage"
            ),
            BugPattern(
                "mutable_default_arg",
                r"def\s+\w+\s*\([^)]*=\s*\[\]",
                "medium",
                "Mutable default argument (list) - can cause unexpected behavior",
                "python"
            ),
            
            # JavaScript/TypeScript patterns
            BugPattern(
                "console_log",
                r"console\.log\(",
                "low",
                "Console.log statement - should be removed in production",
                "javascript"
            ),
            BugPattern(
                "double_equals",
                r"(?<!!)==(?!=)",
                "medium",
                "Use === instead of == for strict equality",
                "javascript"
            ),
            BugPattern(
                "eval_usage_js",
                r"\beval\s*\(",
                "critical",
                "Use of eval() can lead to arbitrary code execution",
                "javascript"
            ),
            
            # General patterns
            BugPattern(
                "todo_fixme",
                r"(TODO|FIXME|XXX|HACK):",
                "low",
                "Unresolved TODO/FIXME comment"
            ),
            BugPattern(
                "debug_code",
                r"(debugger|import\s+pdb)",
                "medium",
                "Debug code should be removed before production"
            ),
        ]
    
    def detect_bugs_in_code(self, code: str, language: Optional[str] = None, 
                           filepath: Optional[str] = None) -> Dict:
        """
        Detect bugs using pattern matching
        
        Args:
            code: Source code to analyze
            language: Programming language (optional, auto-detected from filepath)
            filepath: File path (optional, used for language detection)
            
        Returns:
            Dictionary with findings
        """
        if not language and filepath:
            language = self._detect_language(filepath)
        
        findings = []
        for pattern in self.patterns:
            matches = pattern.check(code, language)
            findings.extend(matches)
        
        # Sort by severity
        severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        findings.sort(key=lambda x: severity_order.get(x['severity'], 4))
        
        return {
            'filepath': filepath,
            'language': language,
            'total_findings': len(findings),
            'critical': len([f for f in findings if f['severity'] == 'critical']),
            'high': len([f for f in findings if f['severity'] == 'high']),
            'medium': len([f for f in findings if f['severity'] == 'medium']),
            'low': len([f for f in findings if f['severity'] == 'low']),
            'findings': findings,
            'timestamp': datetime.now().isoformat()
        }
    
    def detect_bugs_in_file(self, filepath: str) -> Dict:
        """
        Detect bugs in a file
        
        Args:
            filepath: Path to the file (relative to repository root)
            
        Returns:
            Bug detection results
        """
        try:
            # Validate path to prevent directory traversal
            validated_path = self._validate_file_path(filepath)
            
            with open(validated_path, 'r', encoding='utf-8') as f:
                code = f.read()
            
            return self.detect_bugs_in_code(code, filepath=filepath)
            
        except ValueError as e:
            logger.error(f"Path validation failed for {filepath}: {e}")
            return {
                'filepath': filepath,
                'error': f"Invalid path: {str(e)}",
                'total_findings': 0,
                'findings': []
            }
        except Exception as e:
            logger.error(f"Error reading file {filepath}: {e}")
            return {
                'filepath': filepath,
                'error': str(e),
                'total_findings': 0,
                'findings': []
            }
    
    def ai_bug_analysis(self, code: str, language: Optional[str] = None) -> str:
        """
        Use AI to analyze code for bugs
        
        Args:
            code: Source code to analyze
            language: Programming language
            
        Returns:
            AI analysis report
        """
        if not self.model_name or not _llm_available:
            return "AI analysis not available - no LLM model configured"
        
        lang_context = f" ({language})" if language else ""
        prompt = (
            f"You are an expert code reviewer and bug detector. Analyze the following code{lang_context} "
            f"for potential bugs, security vulnerabilities, and code quality issues.\n\n"
            f"Code:\n```\n{code}\n```\n\n"
            f"Provide a structured analysis with:\n"
            f"1. Potential bugs and their severity (critical/high/medium/low)\n"
            f"2. Security concerns\n"
            f"3. Code quality issues\n"
            f"4. Recommended fixes\n\n"
            f"Be specific and cite line numbers or code snippets when possible."
        )
        
        try:
            analysis = llm.manager.ask_llm(prompt, model_name=self.model_name, max_tokens=1024)
            return analysis
        except Exception as e:
            logger.error(f"AI analysis failed: {e}")
            return f"AI analysis failed: {str(e)}"
    
    def format_findings_report(self, results: Dict) -> str:
        """
        Format bug detection results as a readable report
        
        Args:
            results: Bug detection results
            
        Returns:
            Formatted markdown report
        """
        if 'error' in results:
            return f"❌ **Error analyzing {results.get('filepath', 'file')}:**\n{results['error']}"
        
        filepath = results.get('filepath', 'Code')
        total = results['total_findings']
        
        if total == 0:
            return f"✅ **No issues found in {filepath}**\n\nCode looks clean!"
        
        # Summary
        report = f"🔍 **Bug Detection Report: {filepath}**\n\n"
        report += f"**Summary:**\n"
        report += f"- Total findings: {total}\n"
        report += f"- Critical: {results['critical']}\n"
        report += f"- High: {results['high']}\n"
        report += f"- Medium: {results['medium']}\n"
        report += f"- Low: {results['low']}\n\n"
        
        # Findings by severity
        severity_emoji = {
            'critical': '🔴',
            'high': '🟠',
            'medium': '🟡',
            'low': '🔵'
        }
        
        findings_by_severity = {}
        for finding in results['findings']:
            severity = finding['severity']
            if severity not in findings_by_severity:
                findings_by_severity[severity] = []
            findings_by_severity[severity].append(finding)
        
        for severity in ['critical', 'high', 'medium', 'low']:
            if severity in findings_by_severity:
                findings = findings_by_severity[severity]
                report += f"### {severity_emoji[severity]} {severity.upper()} ({len(findings)})\n\n"
                
                for finding in findings[:5]:  # Limit to first 5 per severity
                    report += f"**Line {finding['line']}** - {finding['pattern']}\n"
                    report += f"- {finding['description']}\n"
                    report += f"- Code: `{finding['code'][:100]}`\n\n"
                
                if len(findings) > 5:
                    report += f"*... and {len(findings) - 5} more {severity} issues*\n\n"
        
        return report
    
    def format_proactive_scan_report(self, results: Dict) -> str:
        """
        Format proactive scan results as a readable report
        
        Args:
            results: Proactive scan results
            
        Returns:
            Formatted markdown report
        """
        if 'error' in results:
            return f"❌ **Error scanning directory:**\n{results['error']}"
        
        directory = results.get('directory', 'unknown')
        files_scanned = results.get('files_scanned', 0)
        total = results.get('total_findings', 0)
        
        report = f"🔍 **Proactive Bug Scan: {directory}**\n\n"
        report += f"**Summary:**\n"
        report += f"- Files scanned: {files_scanned}\n"
        report += f"- Total findings: {total}\n"
        report += f"- Critical: {results.get('critical', 0)}\n"
        report += f"- High: {results.get('high', 0)}\n"
        report += f"- Medium: {results.get('medium', 0)}\n"
        report += f"- Low: {results.get('low', 0)}\n\n"
        
        if total == 0:
            report += "✅ **No issues found!** All scanned files are clean.\n"
            return report
        
        # Show top files with issues
        files_with_issues = results.get('files_with_issues', [])
        if files_with_issues:
            report += f"**Files with issues ({len(files_with_issues)}):**\n\n"
            
            # Sort by total findings
            sorted_files = sorted(files_with_issues, key=lambda x: x['total_findings'], reverse=True)
            
            for idx, file_result in enumerate(sorted_files[:10], 1):  # Show top 10
                filepath = file_result.get('filepath', 'unknown')
                file_total = file_result.get('total_findings', 0)
                critical = file_result.get('critical', 0)
                high = file_result.get('high', 0)
                
                emoji = "🔴" if critical > 0 else ("🟠" if high > 0 else "🟡")
                report += f"{idx}. {emoji} `{filepath}` - {file_total} issues"
                if critical > 0:
                    report += f" ({critical} critical)"
                elif high > 0:
                    report += f" ({high} high)"
                report += "\n"
            
            if len(sorted_files) > 10:
                report += f"\n*... and {len(sorted_files) - 10} more files with issues*\n"
            
            report += "\n💡 **Tip:** Use 'Find bugs in file <filepath>' to see detailed analysis of a specific file.\n"
        
        return report
    
    def _detect_language(self, filepath: str) -> Optional[str]:
        """Detect programming language from file extension"""
        ext_map = {
            '.py': 'python',
            '.js': 'javascript',
            '.ts': 'typescript',
            '.jsx': 'javascript',
            '.tsx': 'typescript',
            '.java': 'java',
            '.c': 'c',
            '.cpp': 'cpp',
            '.h': 'c',
            '.hpp': 'cpp',
            '.go': 'go',
            '.rs': 'rust',
            '.rb': 'ruby',
            '.php': 'php',
        }
        
        ext = os.path.splitext(filepath)[1].lower()
        return ext_map.get(ext)
    
    def proactive_scan_directory(self, directory: str, extensions: List[str] = None) -> Dict:
        """
        Proactively scan a directory for bugs
        
        Args:
            directory: Directory path to scan (relative to repository root)
            extensions: File extensions to scan (default: common code files)
            
        Returns:
            Aggregated scan results
        """
        if extensions is None:
            extensions = ['.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.go', '.rs', '.rb', '.php']
        
        # Validate directory path
        try:
            validated_dir = self._validate_file_path(directory)
        except ValueError as e:
            logger.error(f"Path validation failed for directory {directory}: {e}")
            return {
                'directory': directory,
                'error': f"Invalid path: {str(e)}",
                'files_scanned': 0,
                'total_findings': 0
            }
        
        all_results = []
        total_files_scanned = 0
        total_critical = 0
        total_high = 0
        total_medium = 0
        total_low = 0
        
        try:
            for root, _, files in os.walk(validated_dir):
                for file in files:
                    if any(file.endswith(ext) for ext in extensions):
                        total_files_scanned += 1
                        filepath = os.path.join(root, file)
                        # Use relative path for reporting
                        rel_path = os.path.relpath(filepath, self.repo_path)
                        results = self.detect_bugs_in_code(
                            open(filepath, 'r', encoding='utf-8').read(),
                            filepath=rel_path
                        )
                        
                        if results['total_findings'] > 0:
                            all_results.append(results)
                            total_critical += results['critical']
                            total_high += results['high']
                            total_medium += results['medium']
                            total_low += results['low']
        
        except Exception as e:
            logger.error(f"Error scanning directory {directory}: {e}")
            return {'error': str(e)}
        
        return {
            'directory': directory,
            'files_scanned': total_files_scanned,
            'total_findings': total_critical + total_high + total_medium + total_low,
            'critical': total_critical,
            'high': total_high,
            'medium': total_medium,
            'low': total_low,
            'files_with_issues': all_results,
            'timestamp': datetime.now().isoformat()
        }


# Global instance
_bug_detector = None


def get_bug_detector() -> BugDetector:
    """Get or create the global bug detector instance"""
    global _bug_detector
    if _bug_detector is None:
        _bug_detector = BugDetector()
    return _bug_detector


def detect_bugs(code: str, language: Optional[str] = None, filepath: Optional[str] = None) -> Dict:
    """
    Convenience function to detect bugs in code
    
    Args:
        code: Source code to analyze
        language: Programming language
        filepath: File path
        
    Returns:
        Bug detection results
    """
    detector = get_bug_detector()
    return detector.detect_bugs_in_code(code, language, filepath)
