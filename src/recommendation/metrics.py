#!/usr/bin/python

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

def init_metrics(meter):

    # Recommendations counter
    app_recommendations_counter = meter.create_counter(
        'app_recommendations_counter', unit='recommendations', description="Counts the total number of given recommendations"
    )

    # Cache hits counter
    app_cache_hits_total = meter.create_counter(
        'app_cache_hits_total', unit='hits', description='Counts the total number of cache hits in the recommendation service'
    )

    # Cache misses counter
    app_cache_misses_total = meter.create_counter(
        'app_cache_misses_total', unit='misses', description='Counts the total number of cache misses in the recommendation service'
    )

    rec_svc_metrics = {
        "app_recommendations_counter": app_recommendations_counter,
        "app_cache_hits_total": app_cache_hits_total,
    }

    return rec_svc_metrics


