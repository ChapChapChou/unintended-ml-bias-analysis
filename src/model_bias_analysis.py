"""Analysis of model bias.

We look at differences in model scores as a way to compare bias in different
models.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import datetime
import os

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn import metrics


def compute_auc(y_true, y_pred):
  try:
    return metrics.roc_auc_score(y_true, y_pred)
  except ValueError:
    return np.nan

### Per-subgroup pinned AUC analysis.
def model_family_auc(dataset, model_names, label_col):
    aucs = [compute_auc(dataset[label_col], dataset[model_name])
            for model_name in model_names]
    return {
        'aucs': aucs,
        'mean': np.mean(aucs),
        'median': np.median(aucs),
        'std': np.std(aucs),
    }


def plot_model_family_auc(dataset, model_names, label_col, min_auc=0.9):
    result = model_family_auc(dataset, model_names, label_col)
    print('mean AUC:', result['mean'])
    print('median:', result['median'])
    print('stddev:', result['std'])
    plt.hist(result['aucs'])
    plt.gca().set_xlim([min_auc, 1.0])
    plt.show()
    return result


def read_identity_terms(identity_terms_path):
    with open(identity_terms_path) as f:
        return [term.strip() for term in f.readlines()]

    
def add_subgroup_columns_from_text(df, text_column, subgroups):
    for term in subgroups:
        df[term] = df[text_column].apply(lambda x: term in x)
    
    
def balanced_subgroup_subset(df, subgroup, text_col):
    """Returns data subset containing subgroup balanced with sample of other data.

    We draw a random sample from the dataset of other examples because we don't
    care about the model's ability to distinguish toxic from non-toxic just
    within the subgroup-specific dataset, but rather its ability to distinguish for
    the subgroup-specific subset within the context of a larger distribution of
    data.
    """
    subgroup_df = df[df[subgroup]]
    nonsubgroup_df = df[~df[subgroup]].sample(len(subgroup_df), random_state=25)
    combined = pd.concat([subgroup_df, nonsubgroup_df])
    return combined


def model_family_name(model_names):
    """Given a list of model names, returns the common prefix."""
    prefix = os.path.commonprefix(model_names)
    if not prefix:
        raise ValueError("couldn't determine family name from model names")
    return prefix.strip('_')


def per_subgroup_aucs(dataset, subgroups, model_families, text_col, label_col):
    """Computes per-subgroup 'pinned' AUC scores for each model family."""
    records = []
    for subgroup in subgroups:
        subgroup_subset = balanced_subgroup_subset(dataset, subgroup, text_col)
        subgroup_record = {'subgroup': subgroup, 'subset_size': len(subgroup_subset)}
        for model_family in model_families:
            family_name = model_family_name(model_family)
            aucs = [compute_auc(subgroup_subset[label_col], subgroup_subset[model_name])
                    for model_name in model_family]
            subgroup_record.update({
                family_name + '_mean': np.mean(aucs),
                family_name + '_median': np.median(aucs),
                family_name + '_std': np.std(aucs),
                family_name + '_aucs': aucs,
            })
        records.append(subgroup_record)
    return pd.DataFrame(records)
    

### Equality of opportunity negative rates analysis.

def confusion_matrix_counts(df, score_col, label_col, threshold):
    return {
        'tp': len(df[(df[score_col] >= threshold) & (df[label_col] == True)]),
        'tn': len(df[(df[score_col] < threshold) & (df[label_col] == False)]),
        'fp': len(df[(df[score_col] >= threshold) & (df[label_col] == False)]),
        'fn': len(df[(df[score_col] < threshold) & (df[label_col] == True)]),
    }


# https://en.wikipedia.org/wiki/Confusion_matrix
def compute_confusion_rates(df, score_col, label_col, threshold):
    confusion = confusion_matrix_counts(df, score_col, label_col, threshold)
    actual_positives = confusion['tp'] + confusion['fn']
    actual_negatives = confusion['tn'] + confusion['fp']
    # True positive rate, sensitivity, recall.
    tpr = confusion['tp'] / actual_positives
    # True negative rate, specificity.
    tnr = confusion['tn'] / actual_negatives
    # False positive rate, fall-out.
    fpr = 1 - tnr
    # False negative rate, miss rate.
    fnr = 1 - tpr
    # Precision, positive predictive value.
    precision = confusion['tp'] / (confusion['tp'] +  confusion['fp'])
    return {
        'tpr': tpr,
        'tnr': tnr,
        'fpr': fpr,
        'fnr': fnr,
        'precision': precision,
        'recall': tpr,
    }

def compute_equal_error_rate(df, score_col, label_col, num_thresholds=101):
    """Returns threshold where the false negative and false positive counts are equal."""
    # Note: I'm not sure if this should be based on the false positive/negative
    # *counts*, or the *rates*. However, they should be equivalent for balanced
    # datasets.
    thresholds = np.linspace(0, 1, num_thresholds)
    min_threshold = None
    min_confusion_matrix = None
    min_diff = float('inf')
    for threshold in thresholds:
        confusion_matrix = confusion_matrix_counts(df, score_col, label_col,
                                                   threshold)
        difference = abs(confusion_matrix['fn'] - confusion_matrix['fp'])
        if difference <= min_diff:
            min_diff = difference
            min_confusion_matrix = confusion_matrix
            min_threshold = threshold
        else:
            # min_diff should be monotonically non-decreasing, so once it
            # increases we can break. Yes, we could do a binary search instead.
            break
    return {
        'threshold': min_threshold,
        'confusion_matrix': min_confusion_matrix,
    }

def per_model_eer(dataset, label_col, model_names, num_eer_thresholds=101):
    """Computes the equal error rate for every model on the given dataset."""
    model_name_to_eer = {}
    for model_name in model_names:
        eer = compute_equal_error_rate(dataset, model_name, label_col,
                                       num_eer_thresholds)
        model_name_to_eer[model_name] = eer['threshold']
    return model_name_to_eer

def per_subgroup_negative_rates(df, subgroups, model_families, threshold, text_col,
                            label_col):
    """Computes per-subgroup true/false negative rates for all model families.

    Args:
      df: dataset to compute rates on.
      subgroups: negative rates are computed on subsets of the dataset containing
          each subgroup.
      text_col: column in df containing the text.
      label_col: column in df containing the boolean label.
      model_families: list of model families; each model family is a list of
          model names in the family.
      threshold: threshold to use to compute negative rates. Can either be a
          float, or a dictionary mapping model name to float threshold in order
          to use a different threshold for each model.

    Returns:
      DataFrame with per-subgroup false/true negative rates for each model family.
          Results are summarized across each model family, giving mean, median,
          and standard deviation of each negative rate.
    """
    records = []
    for subgroup in subgroups:
        if subgroup is None:
            subgroup_subset = df
        else:
            subgroup_subset = df[df[subgroup]]
        subgroup_record = {'subgroup': subgroup, 'subset_size': len(subgroup_subset)}
        for model_family in model_families:
            family_name = model_family_name(model_family)
            family_rates = []
            for model_name in model_family:
                model_threshold = (threshold[model_name]
                                   if isinstance(threshold, dict) else
                                   threshold)
                assert isinstance(model_threshold, float)
                model_rates = compute_confusion_rates(
                    subgroup_subset, model_name, label_col, model_threshold)
                family_rates.append(model_rates)
            tnrs, fnrs = ([rates['tnr'] for rates in family_rates],
                          [rates['fnr'] for rates in family_rates])
            subgroup_record.update({
                family_name + '_tnr_median': np.median(tnrs),
                family_name + '_tnr_mean': np.mean(tnrs),
                family_name + '_tnr_std': np.std(tnrs),
                family_name + '_tnr_values': tnrs,
                family_name + '_fnr_median': np.median(fnrs),
                family_name + '_fnr_mean': np.mean(fnrs),
                family_name + '_fnr_std': np.std(fnrs),
                family_name + '_fnr_values': fnrs,
            })
        records.append(subgroup_record)
    return pd.DataFrame(records)

### Summary metrics
def diff_per_subgroup_from_overall(overall_metrics, per_subgroup_metrics, model_families, metric_column):
    """Computes the sum of differences between the per-subgroup metric values and the overall values 
    summed over all subgroups and models. i.e. sum(|overall_i - per-subgroup_i,t|) for i in model
    instances and t in subgroups.
    
    Args:
      overall_metrics: dict of model familiy to list of score values for the overall 
          dataset (one per model instance).
      per_subgroup_metrics: DataFrame of scored results, one subgroup per row. Expected to have
          a column named model family name + metric column, which contains a list of 
          one score per model instance.
      model_families: list of model families; each model family is a list of
          model names in the family.
      metric_column: column name suffix in the per_subgroup_metrics df where the per-subgroup data 
          to be diffed is stored.
    
    Returns:
      A dictionary of model family name to sum of differences value for that model family.
    """
    diffs = {}
    for fams in model_families:
        family_name = model_family_name(fams)
        family_overall_metrics = overall_metrics[family_name]
        metric_diff_sum = 0.0
        diffs[family_name] = 0.0
        # Loop over the subgroups. one_subgroup_metric_list is a list of the per-subgroup
        # values, one per model instance.
        for one_subgroup_metric_list in per_subgroup_metrics[family_name + metric_column]:
            # Zips the overall scores with the per-subgroup scores, pairing results
            # from the same model instance, then diffs those pairs and sums.
            per_subgroup_metric_diffs = [abs(overall_score - per_subgroup_score)
                                         for overall_score, per_subgroup_score
                                             in zip(family_overall_metrics, one_subgroup_metric_list)]
            diffs[family_name] += sum(per_subgroup_metric_diffs)
    return diffs

def per_subgroup_auc_diff_from_overall(dataset, subgroups, model_families):
    """Calculates the sum of differences between the per-subgroup pinned AUC and the overall AUC."""
    per_subgroup_auc_results = per_subgroup_aucs(dataset, subgroups, model_families, 'text', 'label')
    overall_aucs = {}
    for fams in model_families:
        family_name = model_family_name(fams)
        overall_aucs[family_name] = model_family_auc(dataset, fams, 'label')['aucs']
    return diff_per_subgroup_from_overall(overall_aucs, per_subgroup_auc_results, model_families, '_aucs')

def per_subgroup_nr_diff_from_overall(df, subgroups, model_families, threshold, metric_column):
    """Calculates the sum of differences between the per-subgroup true or false negative rate and the overall rate."""
    per_subgroup_nrs = per_subgroup_negative_rates(
        df, subgroups, model_families, threshold, 'text', 'label')
    all_nrs = per_subgroup_negative_rates(
        df, [None], model_families, threshold, 'text', 'label')
    overall_nrs = {}
    for fams in model_families:
        family_name = model_family_name(fams)
        overall_nrs[family_name] = all_nrs[family_name + metric_column][0]
    return diff_per_subgroup_from_overall(overall_nrs, per_subgroup_nrs, model_families, metric_column)

def per_subgroup_fnr_diff_from_overall(df, subgroups, model_families, threshold):
    """Calculates the sum of differences between the per-subgroup false negative rate and the overall FNR."""
    return per_subgroup_nr_diff_from_overall(df, subgroups, model_families, threshold, '_fnr_values')

def per_subgroup_tnr_diff_from_overall(df, subgroups, model_families, threshold):
    """Calculates the sum of differences between the per-subgroup true negative rate and the overall TNR."""    
    return per_subgroup_nr_diff_from_overall(df, subgroups, model_families, threshold, '_tnr_values')

### Plotting.

def per_subgroup_scatterplots(df, subgroup_col, values_col, title='', y_lim=(0.8, 1.0),
                          figsize=(15,5), point_size=8, file_name='plot'):
    """Displays a series of one-dimensional scatterplots, 1 scatterplot per subgroup.

    Args:
      df: DataFrame contain subgroup_col and values_col.
      subgroup_col: Column containing subgroups.
      values_col: Column containing collection of values to plot (each cell
          should contain a sequence of values, e.g. the AUCs for multiple models
          from the same family).
      title: Plot title.
      y_lim: Plot bounds for y axis.
      figsize: Plot figure size.
    """
    fig = plt.figure(figsize=figsize)
    ax = fig.add_subplot(111)
    for i, (_index, row) in enumerate(df.iterrows()):
        # For each subgroup, we plot a 1D scatterplot. The x-value is the position
        # of the item in the dataframe. To change the ordering of the subgroups,
        # sort the dataframe before passing to this function.
        x = [i] * len(row[values_col])
        y = row[values_col]
        ax.scatter(x, y, s=point_size)
    ax.set_xticklabels(df[subgroup_col], rotation=90)
    ax.set_xticks(range(len(df)))
    ax.set_ylim(y_lim)
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig('/tmp/%s_%s.eps' % (file_name, values_col), format='eps')
