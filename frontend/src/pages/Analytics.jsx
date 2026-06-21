import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  LineChart, Line, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, RadarChart, PolarGrid,
  PolarAngleAxis, PolarRadiusAxis, Radar
} from 'recharts';
import { apiUrl, apiFetch } from '../auth';

export default function Analytics() {
  const [personalStats, setPersonalStats] = useState(null);
  const [trends, setTrends] = useState(null);
  const [boardMetrics, setBoardMetrics] = useState(null);
  const [marketInsights, setMarketInsights] = useState(null);
  const [salaryTrends, setSalaryTrends] = useState(null);
  const [skillsDemand, setSkillsDemand] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchAnalytics();
  }, []);

  const fetchAnalytics = async () => {
    try {
      const [personalRes, boardRes, marketRes, salaryRes, skillsRes] = await Promise.all([
        apiFetch(apiUrl('/api/analytics/personal')),
        apiFetch(apiUrl('/api/analytics/board-performance')),
        fetch(apiUrl('/api/analytics/market')),
        fetch(apiUrl('/api/analytics/salary-trends')),
        fetch(apiUrl('/api/analytics/skills-demand')),
      ]);

      if (personalRes.ok) {
        const data = await personalRes.json();
        setPersonalStats(data.stats);
        setTrends({
          applications: data.application_trends,
          interviews: data.interview_trends,
          offers: data.offer_trends,
        });
      }
      if (boardRes.ok) {
        const data = await boardRes.json();
        setBoardMetrics(data.board_metrics);
      }
      if (marketRes.ok) {
        const data = await marketRes.json();
        setMarketInsights(data);
      }
      if (salaryRes.ok) {
        const data = await salaryRes.json();
        setSalaryTrends(data.salary_trends);
      }
      if (skillsRes.ok) {
        const data = await skillsRes.json();
        setSkillsDemand(data.skills);
      }

      setLoading(false);
    } catch (error) {
      console.error('Failed to fetch analytics:', error);
      setLoading(false);
    }
  };

  if (loading) {
    return <div className="analytics-container"><p>Loading analytics...</p></div>;
  }

  const statCards = personalStats ? [
    { label: 'Applications', value: personalStats.applications_count, color: '#3b82f6' },
    { label: 'Interviewing', value: personalStats.interviews_count, color: '#8b5cf6' },
    { label: 'Offers', value: personalStats.offers_count, color: '#10b981' },
    { label: 'Rejected', value: personalStats.rejected_count, color: '#ef4444' },
  ] : [];

  const colors = ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444', '#06b6d4', '#ec4899', '#6366f1'];

  return (
    <div className="analytics-container">
      <Link to="/" className="back-link" style={{ display: 'inline-block', marginBottom: 16, color: 'var(--primary)', textDecoration: 'none', fontWeight: 500 }}>← Back to Dashboard</Link>
      <h1>Analytics & Insights</h1>

      {/* Personal Stats Cards */}
      {personalStats && (
        <div className="stats-grid">
          {statCards.map((stat, idx) => (
            <div key={idx} className="stat-card" style={{ borderLeftColor: stat.color }}>
              <h3>{stat.label}</h3>
              <p className="stat-value">{stat.value}</p>
              {personalStats.avg_score && stat.label === 'Applications' && (
                <p className="stat-detail">Avg Score: {personalStats.avg_score.toFixed(1)}%</p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Application Trends */}
      {trends && (
        <div className="chart-section">
          <h2>Application Trends (Last 30 Days)</h2>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={trends.applications}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" />
              <YAxis />
              <Tooltip />
              <Legend />
              <Line
                type="monotone"
                dataKey="count"
                stroke="#3b82f6"
                dot={{ r: 4 }}
                name="Applications"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Interview & Offer Trends */}
      {trends && (
        <div className="chart-section">
          <h2>Interview & Offer Trends</h2>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={trends.interviews}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" />
              <YAxis />
              <Tooltip />
              <Legend />
              <Bar dataKey="count" fill="#8b5cf6" name="Interviews" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Job Board Performance */}
      {boardMetrics && boardMetrics.length > 0 && (
        <div className="chart-section">
          <h2>Job Board Performance</h2>
          <div className="board-metrics-table">
            <table>
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Applications</th>
                  <th>Offers</th>
                  <th>Conversion Rate</th>
                </tr>
              </thead>
              <tbody>
                {boardMetrics.map((metric, idx) => (
                  <tr key={idx}>
                    <td className="source-cell">{metric.source}</td>
                    <td>{metric.applications}</td>
                    <td>{metric.offers}</td>
                    <td>{metric.conversion_rate.toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Top Skills */}
      {marketInsights && marketInsights.top_skills && (
        <div className="chart-section">
          <h2>Top In-Demand Skills</h2>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={marketInsights.top_skills} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis type="number" />
              <YAxis type="category" dataKey="skill" width={120} />
              <Tooltip />
              <Bar dataKey="count" fill="#10b981" name="Job Postings" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Salary Trends */}
      {salaryTrends && salaryTrends.length > 0 && (
        <div className="chart-section">
          <h2>Salary Trends by Location</h2>
          <ResponsiveContainer width="100%" height={400}>
            <BarChart data={salaryTrends}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="location" angle={-45} textAnchor="end" height={100} />
              <YAxis />
              <Tooltip />
              <Legend />
              <Bar dataKey="avg_min" fill="#3b82f6" name="Avg Min Salary" />
              <Bar dataKey="avg_max" fill="#10b981" name="Avg Max Salary" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Top Locations */}
      {marketInsights && marketInsights.top_locations && (
        <div className="chart-section">
          <h2>Top Job Locations</h2>
          <ResponsiveContainer width="100%" height={300}>
            <PieChart>
              <Pie
                data={marketInsights.top_locations}
                dataKey="count"
                nameKey="location"
                cx="50%"
                cy="50%"
                outerRadius={100}
                label={({ location, count }) => `${location}: ${count}`}
              >
                {marketInsights.top_locations.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={colors[index % colors.length]} />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Skills Demand Radar */}
      {skillsDemand && skillsDemand.length > 0 && (
        <div className="chart-section">
          <h2>Skills Demand Radar</h2>
          <ResponsiveContainer width="100%" height={400}>
            <RadarChart data={skillsDemand.slice(0, 10)}>
              <PolarGrid />
              <PolarAngleAxis dataKey="skill" />
              <PolarRadiusAxis />
              <Radar
                name="Demand"
                dataKey="demand"
                stroke="#8b5cf6"
                fill="#8b5cf6"
                fillOpacity={0.6}
              />
              <Tooltip />
            </RadarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
