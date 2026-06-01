import React, { useState } from 'react'
import { getScreenshotFileUrl } from '../api/client.js'

export default function VisualRegressionPanel({ screenshots = [], visualAnalysis = null }) {
  const [lightboxSrc, setLightboxSrc] = useState(null)

  if (screenshots.length === 0) return null

  return (
    <div style={{
      background: '#ffffff',
      borderRadius: '0.5rem',
      boxShadow: '0 1px 3px 0 rgba(0,0,0,0.1), 0 1px 2px 0 rgba(0,0,0,0.06)',
      padding: '1.5rem',
      marginBottom: '1.5rem',
    }}>
      <h2 style={{
        fontSize: '0.875rem',
        fontWeight: 600,
        color: '#374151',
        marginBottom: '1rem',
        marginTop: 0,
      }}>
        Visual Analysis
      </h2>

      {/* Thumbnail row */}
      <div style={{
        display: 'flex',
        flexDirection: 'row',
        gap: '0.75rem',
        overflowX: 'auto',
        paddingBottom: '0.5rem',
        marginBottom: '1rem',
      }}>
        {screenshots.map((screenshot) => (
          <img
            key={screenshot.id}
            src={getScreenshotFileUrl(screenshot.id)}
            alt={screenshot.original_filename}
            onClick={() => setLightboxSrc(getScreenshotFileUrl(screenshot.id))}
            style={{
              maxHeight: '160px',
              cursor: 'pointer',
              borderRadius: '0.375rem',
              border: '1px solid #e5e7eb',
              flexShrink: 0,
              objectFit: 'contain',
            }}
          />
        ))}
      </div>

      {/* Analysis section */}
      {visualAnalysis !== null && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {/* Regression badge + confidence */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
            {visualAnalysis.has_regression ? (
              <span style={{
                color: '#b91c1c',
                fontWeight: 600,
                fontSize: '0.875rem',
              }}>
                ⚠ Regression Detected
              </span>
            ) : (
              <span style={{
                color: '#15803d',
                fontWeight: 600,
                fontSize: '0.875rem',
              }}>
                ✓ No Regression
              </span>
            )}
            {visualAnalysis.confidence != null && (
              <span style={{ fontSize: '0.875rem', color: '#6b7280' }}>
                Confidence: {Math.round(visualAnalysis.confidence * 100)}%
              </span>
            )}
          </div>

          {/* Regression description box */}
          {visualAnalysis.has_regression && visualAnalysis.regression_description && (
            <div style={{
              background: '#fefce8',
              border: '1px solid #fde047',
              borderRadius: '0.375rem',
              padding: '0.75rem 1rem',
              fontSize: '0.875rem',
              color: '#713f12',
            }}>
              {visualAnalysis.regression_description}
            </div>
          )}

          {/* Affected components */}
          {visualAnalysis.affected_components?.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', alignItems: 'center' }}>
              <span style={{ fontSize: '0.8125rem', color: '#6b7280', fontWeight: 500 }}>
                Affected:
              </span>
              {visualAnalysis.affected_components.map((component) => (
                <span
                  key={component}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    padding: '0.125rem 0.625rem',
                    borderRadius: '9999px',
                    fontSize: '0.75rem',
                    fontWeight: 500,
                    background: '#f3f4f6',
                    color: '#374151',
                  }}
                >
                  {component}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Lightbox */}
      {lightboxSrc && (
        <div
          onClick={() => setLightboxSrc(null)}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0, 0, 0, 0.75)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 9999,
          }}
        >
          <img
            src={lightboxSrc}
            alt="Screenshot full view"
            style={{
              maxWidth: '90vw',
              maxHeight: '90vh',
              borderRadius: '0.375rem',
              objectFit: 'contain',
            }}
          />
        </div>
      )}
    </div>
  )
}
