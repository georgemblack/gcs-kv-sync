# GCS to KV Sync

The purpose of this service is to provide a one-way sync from Google Cloud Storage to Cloudflare Workers KV.

The service:

* Recives a storage event via Pub/Sub
* Uploads new bucket objects to KV
* Removes deleted bucket objects from KV

The service will also include `mimeType` and `cacheControl` metadata when writing to KV.
