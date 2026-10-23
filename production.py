"""Fail-closed production factory; no credentials are generated or printed."""
import os
from wsgi import create_application
def application_factory():
 if os.environ.get('NOTTA_DATABASE_BACKEND')!='postgres':raise RuntimeError('Production requires explicit postgres backend')
 if os.environ.get('NOTTA_COMMERCIAL_HOST_APPROVED')!='yes':raise RuntimeError('Commercial hosting approval required')
 dsn=os.environ.get('NOTTA_DATABASE_URL');origin=os.environ.get('NOTTA_PUBLIC_ORIGIN')
 if not origin and os.environ.get('VERCEL_ENV')=='preview' and os.environ.get('VERCEL_URL'):origin='https://'+os.environ['VERCEL_URL']
 if not origin and os.environ.get('VERCEL_PROJECT_PRODUCTION_URL'):origin='https://'+os.environ['VERCEL_PROJECT_PRODUCTION_URL']
 if not dsn or not origin or not origin.startswith('https://'):raise RuntimeError('Production database and HTTPS origin are required')
 return create_application(dsn,origin,backend='postgres',initialize=False)
_application=None
def application(environ,start_response):
 global _application
 if _application is None:_application=application_factory()
 return _application(environ,start_response)
