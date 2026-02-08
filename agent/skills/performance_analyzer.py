# agent/skills/performance_analyzer.py

"""
Performance Analysis Skill - Code optimization and performance analysis
Analyzes code complexity, performance bottlenecks, and provides optimization suggestions
"""

import os
import re
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime

# Import llm.manager conditionally
try:
    import llm.manager
    _llm_available = True
except ImportError:
    _llm_available = False

logger = logging.getLogger(__name__)


class PerformanceAnalyzer:
    """
    Performance Analysis and Optimization System
    Analyzes code complexity, resource usage, and provides optimization suggestions
    """
    
    def __init__(self):
        self.model_name = os.getenv("CODING_MODEL_NAME")
        if not self.model_name:
            try:
                from agent.skills.coder import get_coding_model_name
                self.model_name = get_coding_model_name()
            except ImportError:
                self.model_name = None
    
    def analyze_complexity(self, code: str, language: Optional[str] = None) -> Dict:
        """
        Analyze code complexity
        
        Args:
            code: Source code to analyze
            language: Programming language
            
        Returns:
            Complexity analysis results
        """
        results = {
            'lines_of_code': len(code.split('\n')),
            'cyclomatic_complexity': self._estimate_cyclomatic_complexity(code),
            'nested_depth': self._calculate_max_nesting_depth(code),
            'function_count': self._count_functions(code, language),
            'class_count': self._count_classes(code, language),
            'comment_ratio': self._calculate_comment_ratio(code, language),
        }
        
        # Overall complexity rating
        results['complexity_rating'] = self._rate_complexity(results)
        results['recommendations'] = self._get_complexity_recommendations(results)
        
        return results
    
    def analyze_performance(self, code: str, language: Optional[str] = None) -> Dict:
        """
        Analyze code performance and identify bottlenecks
        
        Args:
            code: Source code to analyze
            language: Programming language
            
        Returns:
            Performance analysis results
        """
        issues = []
        
        # Check for common performance issues
        issues.extend(self._check_loop_performance(code, language))
        issues.extend(self._check_string_concatenation(code, language))
        issues.extend(self._check_inefficient_algorithms(code, language))
        issues.extend(self._check_resource_usage(code, language))
        
        # Calculate Big O complexity estimates
        big_o_estimates = self._estimate_big_o_complexity(code, language)
        
        return {
            'issues': issues,
            'big_o_estimates': big_o_estimates,
            'total_issues': len(issues),
            'severity_breakdown': self._categorize_by_severity(issues),
            'timestamp': datetime.now().isoformat()
        }
    
    def suggest_optimizations(self, code: str, language: Optional[str] = None) -> List[Dict]:
        """
        Suggest specific optimizations for the code
        
        Args:
            code: Source code to analyze
            language: Programming language
            
        Returns:
            List of optimization suggestions
        """
        suggestions = []
        
        # Algorithm optimizations
        suggestions.extend(self._suggest_algorithm_improvements(code, language))
        
        # Data structure optimizations
        suggestions.extend(self._suggest_data_structure_improvements(code, language))
        
        # Memory optimizations
        suggestions.extend(self._suggest_memory_improvements(code, language))
        
        # Caching opportunities
        suggestions.extend(self._suggest_caching_opportunities(code, language))
        
        # Sort by priority
        suggestions.sort(key=lambda x: {'high': 0, 'medium': 1, 'low': 2}.get(x.get('priority', 'low'), 3))
        
        return suggestions
    
    def ai_performance_analysis(self, code: str, language: Optional[str] = None) -> str:
        """
        Use AI to provide detailed performance analysis
        
        Args:
            code: Source code to analyze
            language: Programming language
            
        Returns:
            AI-generated performance analysis
        """
        if not self.model_name or not _llm_available:
            return "AI analysis not available - no LLM model configured"
        
        lang_context = f" ({language})" if language else ""
        prompt = (
            f"You are an expert performance engineer. Analyze the following code{lang_context} "
            f"for performance issues and optimization opportunities.\n\n"
            f"Code:\n```\n{code}\n```\n\n"
            f"Provide a detailed analysis including:\n"
            f"1. Time complexity (Big O notation) for key operations\n"
            f"2. Space complexity analysis\n"
            f"3. Performance bottlenecks\n"
            f"4. Specific optimization suggestions with code examples\n"
            f"5. Expected performance improvements\n\n"
            f"Be specific and technical. Provide concrete suggestions."
        )
        
        try:
            analysis = llm.manager.ask_llm(prompt, model_name=self.model_name, max_tokens=1536)
            return analysis
        except Exception as e:
            logger.error(f"AI performance analysis failed: {e}")
            return f"AI analysis failed: {str(e)}"
    
    def generate_optimization_report(self, code: str, language: Optional[str] = None, 
                                    filepath: Optional[str] = None) -> str:
        """
        Generate a comprehensive optimization report
        
        Args:
            code: Source code to analyze
            language: Programming language
            filepath: File path (optional)
            
        Returns:
            Formatted markdown report
        """
        filepath_str = filepath or "Code"
        
        # Perform analyses
        complexity = self.analyze_complexity(code, language)
        performance = self.analyze_performance(code, language)
        suggestions = self.suggest_optimizations(code, language)
        
        # Build report
        report = f"⚡ **Performance Analysis Report: {filepath_str}**\n\n"
        
        # Complexity section
        report += "## 📊 Complexity Analysis\n\n"
        report += f"- **Lines of Code:** {complexity['lines_of_code']}\n"
        report += f"- **Cyclomatic Complexity:** {complexity['cyclomatic_complexity']}\n"
        report += f"- **Max Nesting Depth:** {complexity['nested_depth']}\n"
        report += f"- **Functions:** {complexity['function_count']}\n"
        report += f"- **Classes:** {complexity['class_count']}\n"
        report += f"- **Comment Ratio:** {complexity['comment_ratio']:.1%}\n"
        report += f"- **Overall Rating:** {complexity['complexity_rating']}\n\n"
        
        if complexity['recommendations']:
            report += "**Complexity Recommendations:**\n"
            for rec in complexity['recommendations']:
                report += f"- {rec}\n"
            report += "\n"
        
        # Performance issues section
        if performance['issues']:
            report += f"## ⚠️ Performance Issues ({performance['total_issues']})\n\n"
            
            for issue in performance['issues'][:10]:  # Show top 10
                severity_emoji = {'high': '🔴', 'medium': '🟡', 'low': '🔵'}.get(issue.get('severity', 'low'), '⚪')
                report += f"### {severity_emoji} {issue['title']}\n"
                report += f"- **Type:** {issue['type']}\n"
                report += f"- **Impact:** {issue.get('impact', 'Unknown')}\n"
                report += f"- **Description:** {issue['description']}\n"
                if 'line' in issue:
                    report += f"- **Line:** {issue['line']}\n"
                report += "\n"
        else:
            report += "## ✅ Performance Issues\n\nNo significant performance issues detected.\n\n"
        
        # Big O complexity section
        if performance['big_o_estimates']:
            report += "## 🔢 Complexity Estimates\n\n"
            for estimate in performance['big_o_estimates']:
                report += f"- **{estimate['operation']}:** {estimate['complexity']}\n"
                if estimate.get('note'):
                    report += f"  - {estimate['note']}\n"
            report += "\n"
        
        # Optimization suggestions section
        if suggestions:
            report += f"## 💡 Optimization Suggestions ({len(suggestions)})\n\n"
            
            for idx, suggestion in enumerate(suggestions[:8], 1):  # Show top 8
                priority_emoji = {'high': '🔥', 'medium': '⚡', 'low': '💡'}.get(suggestion.get('priority', 'low'), '💡')
                report += f"### {priority_emoji} {idx}. {suggestion['title']}\n"
                report += f"- **Priority:** {suggestion.get('priority', 'medium').title()}\n"
                report += f"- **Expected Improvement:** {suggestion.get('improvement', 'Moderate')}\n"
                report += f"- **Description:** {suggestion['description']}\n"
                if 'example' in suggestion:
                    report += f"- **Example:**\n```\n{suggestion['example']}\n```\n"
                report += "\n"
        
        return report
    
    # Helper methods for complexity analysis
    
    def _estimate_cyclomatic_complexity(self, code: str) -> int:
        """Estimate cyclomatic complexity"""
        # Count decision points
        decision_keywords = ['if', 'elif', 'else', 'for', 'while', 'case', 'catch', '&&', '||', '?']
        count = 1  # Base complexity
        
        for keyword in decision_keywords:
            if keyword in ['&&', '||', '?']:
                count += code.count(keyword)
            else:
                # Count as whole word
                count += len(re.findall(r'\b' + keyword + r'\b', code))
        
        return count
    
    def _calculate_max_nesting_depth(self, code: str) -> int:
        """Calculate maximum nesting depth"""
        max_depth = 0
        current_depth = 0
        
        # Simple brace/indent counting
        for char in code:
            if char in '{([':
                current_depth += 1
                max_depth = max(max_depth, current_depth)
            elif char in '})]':
                current_depth = max(0, current_depth - 1)
        
        return max_depth
    
    def _count_functions(self, code: str, language: Optional[str]) -> int:
        """Count number of functions"""
        patterns = {
            'python': r'\bdef\s+\w+\s*\(',
            'javascript': r'\bfunction\s+\w+\s*\(|\w+\s*:\s*\([^)]*\)\s*=>',
            'java': r'(public|private|protected)?\s*(static)?\s+\w+\s+\w+\s*\(',
        }
        
        pattern = patterns.get(language, r'\bdef\s+\w+\s*\(|\bfunction\s+\w+\s*\(')
        return len(re.findall(pattern, code))
    
    def _count_classes(self, code: str, language: Optional[str]) -> int:
        """Count number of classes"""
        pattern = r'\bclass\s+\w+'
        return len(re.findall(pattern, code))
    
    def _calculate_comment_ratio(self, code: str, language: Optional[str]) -> float:
        """Calculate ratio of comments to code"""
        lines = code.split('\n')
        comment_lines = 0
        
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('//') or stripped.startswith('/*'):
                comment_lines += 1
        
        total_lines = len([l for l in lines if l.strip()])
        return comment_lines / total_lines if total_lines > 0 else 0.0
    
    def _rate_complexity(self, results: Dict) -> str:
        """Rate overall complexity"""
        score = 0
        
        # Cyclomatic complexity scoring
        cc = results['cyclomatic_complexity']
        if cc > 50:
            score += 3
        elif cc > 20:
            score += 2
        elif cc > 10:
            score += 1
        
        # Nesting depth scoring
        depth = results['nested_depth']
        if depth > 5:
            score += 2
        elif depth > 3:
            score += 1
        
        # Lines of code scoring
        loc = results['lines_of_code']
        if loc > 500:
            score += 2
        elif loc > 200:
            score += 1
        
        # Rating
        if score >= 5:
            return "Very Complex - Needs Refactoring"
        elif score >= 3:
            return "Complex - Consider Simplification"
        elif score >= 1:
            return "Moderate - Room for Improvement"
        else:
            return "Simple - Good"
    
    def _get_complexity_recommendations(self, results: Dict) -> List[str]:
        """Get recommendations based on complexity"""
        recommendations = []
        
        if results['cyclomatic_complexity'] > 20:
            recommendations.append("Break down complex functions into smaller, more manageable pieces")
        
        if results['nested_depth'] > 4:
            recommendations.append("Reduce nesting depth by extracting nested logic into separate functions")
        
        if results['lines_of_code'] > 300:
            recommendations.append("Consider splitting this file into multiple modules")
        
        if results['comment_ratio'] < 0.1:
            recommendations.append("Add more comments to explain complex logic")
        
        return recommendations
    
    # Helper methods for performance analysis
    
    def _check_loop_performance(self, code: str, language: Optional[str]) -> List[Dict]:
        """Check for loop performance issues"""
        issues = []
        
        # Nested loops
        nested_loop_pattern = r'for\s+.*:\s*\n\s+for\s+.*:'
        for match in re.finditer(nested_loop_pattern, code, re.MULTILINE):
            line_num = code[:match.start()].count('\n') + 1
            issues.append({
                'type': 'nested_loops',
                'severity': 'medium',
                'title': 'Nested Loops Detected',
                'description': 'Nested loops can be O(n²) or worse. Consider using hash maps or other data structures.',
                'impact': 'Performance degrades quadratically with input size',
                'line': line_num
            })
        
        return issues
    
    def _check_string_concatenation(self, code: str, language: Optional[str]) -> List[Dict]:
        """Check for inefficient string concatenation"""
        issues = []
        
        # Python += in loops
        if language == 'python':
            pattern = r'for\s+.*:.*\n\s+.*\+=.*["\']'
            for match in re.finditer(pattern, code, re.MULTILINE | re.DOTALL):
                line_num = code[:match.start()].count('\n') + 1
                issues.append({
                    'type': 'string_concatenation',
                    'severity': 'medium',
                    'title': 'String Concatenation in Loop',
                    'description': 'Use list append and join() instead of += for better performance',
                    'impact': 'O(n²) time complexity due to string immutability',
                    'line': line_num
                })
        
        return issues
    
    def _check_inefficient_algorithms(self, code: str, language: Optional[str]) -> List[Dict]:
        """Check for inefficient algorithm patterns"""
        issues = []
        
        # Linear search in loop
        if re.search(r'for\s+.*:.*\n\s+if\s+.*\s+in\s+', code, re.MULTILINE | re.DOTALL):
            issues.append({
                'type': 'linear_search',
                'severity': 'medium',
                'title': 'Potential Linear Search in Loop',
                'description': 'Consider using set or dict for O(1) lookups instead of list',
                'impact': 'O(n²) time complexity'
            })
        
        return issues
    
    def _check_resource_usage(self, code: str, language: Optional[str]) -> List[Dict]:
        """Check for resource usage issues"""
        issues = []
        
        # File operations without context manager
        if language == 'python':
            if re.search(r'open\s*\([^)]+\)(?!\s*as\s+)', code):
                issues.append({
                    'type': 'resource_leak',
                    'severity': 'high',
                    'title': 'File Not Using Context Manager',
                    'description': 'Use "with open() as f:" to ensure file is properly closed',
                    'impact': 'Potential resource leaks and file descriptor exhaustion'
                })
        
        return issues
    
    def _estimate_big_o_complexity(self, code: str, language: Optional[str]) -> List[Dict]:
        """Estimate Big O complexity"""
        estimates = []
        
        # Simple heuristics
        if re.search(r'for\s+.*:\s*\n\s+for\s+.*:', code, re.MULTILINE):
            estimates.append({
                'operation': 'Nested loops',
                'complexity': 'O(n²)',
                'note': 'Consider using hash-based lookups or more efficient algorithms'
            })
        elif 'for' in code or 'while' in code:
            estimates.append({
                'operation': 'Single loop',
                'complexity': 'O(n)',
                'note': 'Linear time complexity'
            })
        
        if 'sorted(' in code or '.sort(' in code:
            estimates.append({
                'operation': 'Sorting',
                'complexity': 'O(n log n)',
                'note': 'Efficient sorting algorithm'
            })
        
        return estimates
    
    def _categorize_by_severity(self, issues: List[Dict]) -> Dict:
        """Categorize issues by severity"""
        categories = {'high': 0, 'medium': 0, 'low': 0}
        for issue in issues:
            severity = issue.get('severity', 'low')
            categories[severity] = categories.get(severity, 0) + 1
        return categories
    
    # Helper methods for optimization suggestions
    
    def _suggest_algorithm_improvements(self, code: str, language: Optional[str]) -> List[Dict]:
        """Suggest algorithm improvements"""
        suggestions = []
        
        # Suggest using dict/set for lookups
        if re.search(r'if\s+\w+\s+in\s+\w+', code):
            suggestions.append({
                'title': 'Use Set or Dict for Fast Lookups',
                'priority': 'medium',
                'description': 'Replace list with set for O(1) membership testing instead of O(n)',
                'improvement': '10-100x faster for large datasets',
                'example': 'my_set = set(my_list)  # Convert to set for faster lookups'
            })
        
        return suggestions
    
    def _suggest_data_structure_improvements(self, code: str, language: Optional[str]) -> List[Dict]:
        """Suggest better data structures"""
        suggestions = []
        
        # Suggest deque for queue operations
        if language == 'python' and 'list.pop(0)' in code:
            suggestions.append({
                'title': 'Use collections.deque for Queue Operations',
                'priority': 'high',
                'description': 'list.pop(0) is O(n). Use deque.popleft() for O(1) performance',
                'improvement': 'Up to 100x faster for large queues',
                'example': 'from collections import deque\nqueue = deque(my_list)'
            })
        
        return suggestions
    
    def _suggest_memory_improvements(self, code: str, language: Optional[str]) -> List[Dict]:
        """Suggest memory optimizations"""
        suggestions = []
        
        # Suggest generators
        if language == 'python' and re.search(r'\[.*for.*in.*\]', code):
            suggestions.append({
                'title': 'Consider Using Generators for Large Datasets',
                'priority': 'low',
                'description': 'Replace list comprehension with generator expression to save memory',
                'improvement': 'Reduced memory footprint',
                'example': 'data = (x for x in range(1000000))  # Generator instead of list'
            })
        
        return suggestions
    
    def _suggest_caching_opportunities(self, code: str, language: Optional[str]) -> List[Dict]:
        """Suggest caching opportunities"""
        suggestions = []
        
        # Suggest memoization for recursive functions
        if re.search(r'def\s+\w+\([^)]*\):.*\1\(', code, re.DOTALL):
            suggestions.append({
                'title': 'Add Memoization to Recursive Function',
                'priority': 'high',
                'description': 'Cache results of expensive recursive calls',
                'improvement': 'Exponential to polynomial time complexity',
                'example': 'from functools import lru_cache\n@lru_cache(maxsize=None)\ndef my_function(n):'
            })
        
        return suggestions


# Global instance
_performance_analyzer = None


def get_performance_analyzer() -> PerformanceAnalyzer:
    """Get or create the global performance analyzer instance"""
    global _performance_analyzer
    if _performance_analyzer is None:
        _performance_analyzer = PerformanceAnalyzer()
    return _performance_analyzer
