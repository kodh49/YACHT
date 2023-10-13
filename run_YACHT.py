#!/usr/bin/env python
import os, sys
import numpy as np
import pandas as pd
from pathlib import Path
import srcs.hypothesis_recovery_src as hr
from scipy.sparse import load_npz
import argparse
import srcs.utils as utils
import json
import warnings
warnings.filterwarnings("ignore")
from tqdm import tqdm
from loguru import logger
logger.remove()
logger.add(sys.stdout, format="{time:YYYY-MM-DD HH:mm:ss} - {level} - {message}", level="INFO")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="This script estimates the abundance of microorganisms from a "
                    "reference database matrix and metagenomic sample.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--json', type=str, help='Path to a json file generated by make_training_data_from_sketches.py.', required=True)
    parser.add_argument('--sample_file', help='Metagenomic sample in .sig.zip format', required=True)
    parser.add_argument('--significance', type=float, help='Minimum probability of individual true negative.',
                        required=False, default=0.99)
    parser.add_argument('--num_threads', type=int, help='Number of threads to use for parallelization.', required=False, default=16)
    parser.add_argument('--keep_raw', action='store_true', help='Keep raw results in output file.')
    parser.add_argument('--show_all', action='store_true', help='Show all organisms (no matter if present) in output file.')
    parser.add_argument('--min_coverage_list', nargs="+", type=float, help='A list of percentages of unique k-mers covered by reads in the sample. '
                                                           'Each value should be between 0 and 1, with 0 being the most sensitive (and least '
                                                           'precise) and 1 being the most precise (and least sensitive).', 
                                                           required=False, default=[1, 0.5, 0.1, 0.05, 0.01])
    parser.add_argument('--out_filename', help='output filename', required=False, default='result.xlsx')
    parser.add_argument('--outdir', help='path to output directory', required=True)

    # parse the arguments
    args = parser.parse_args()
    json_file_path = str(Path(args.json).absolute()) # path to json file
    sample_file = str(Path(args.sample_file).absolute()) # location of sample.sig file
    significance = args.significance  # Minimum probability of individual true negative.
    num_threads = args.num_threads  # Number of threads to use for parallelization.
    keep_raw = args.keep_raw  # Keep raw results in output file.
    show_all = args.show_all  # Show all organisms (no matter if present) in output file.
    min_coverage_list = args.min_coverage_list  # a list of percentages of unique k-mers covered by reads in the sample.
    out_filename = args.out_filename  # output filename
    outdir = args.outdir  # csv destination for results

    # check if the json file exists
    utils.check_file_existence(json_file_path, f'Config file {json_file_path} does not exist. '
                                                      f'Please run make_training_data_from_sketches.py first.')
    # load the config file, ksize, and ani_thresh
    config = json.load(open(json_file_path, 'r'))
    manifest_file_path = config['manifest_file_path']
    path_to_temp_dir = config['pathogen_detection_intermediate_files_dir']
    ksize = config['ksize']
    ani_thresh = config['ani_thresh']

    # check if min_coverage is between 0 and 1
    for x in min_coverage_list:
        if not (0 <= x <= 1):
            raise ValueError(f'One of values in the min_coverage_list you provided {x} is not between 0 and 1. Please check your input.')

    # make sure all these files exist
    utils.check_file_existence(manifest_file_path, f'The manifest file {manifest_file_path} '
                                           f'does not exist. Please check if you are using the correct json file as input.')

    # load the training data
    logger.info('Loading the manifest file generated from the training data.')
    manifest = pd.read_csv(manifest_file_path, sep='\t', header=0)

    # load sample signature and its signature info
    logger.info('Loading sample signature and its signature info.')
    sample_sig = utils.load_signature_with_ksize(sample_file, ksize)
    sample_sig_info = utils.get_info_from_single_sig(sample_file, ksize)

    # prep the output data structure, copying over the organism data
    manifest_new = manifest.copy()
    manifest_new['num_exclusive_kmers_in_sample_sketch'] = sample_sig_info[3]
    manifest_new['num_total_kmers_in_sample_sketch'] = utils.get_num_kmers(sample_sig_info[2], sample_sig_info[3], sample_sig_info[4], scale=False)
    manifest_new['sample_scale_factor'] = sample_sig_info[4]
    manifest_new['min_coverage'] = 1

    # check that the sample scale factor is the same as the genome scale factor for all organisms
    sample_diff_idx = np.where(manifest_new['sample_scale_factor'].ne(manifest_new['genome_scale_factor']).to_list())[0].tolist()
    sample_diffs = manifest_new['organism_name'].iloc[sample_diff_idx]
    if not sample_diffs.empty:
        raise ValueError(f'Sample scale factor does not equal genome scale factor for organism '
                         f'{sample_diffs.iloc[0]} and {len(sample_diffs) - 1} others.')

    # compute hypothesis recovery
    logger.info('Computing hypothesis recovery.')
    sample_info_set = (sample_file, sample_sig, sample_sig_info)
    new_manifest = hr.hypothesis_recovery(manifest_new, sample_info_set, path_to_temp_dir, ksize, significance, ani_thresh, num_threads, min_coverage=1)

    # remove unnecessary columns
    remove_cols = ['md5sum','alt_confidence_mut_rate', 'sample_scale_factor'] + [col for col in new_manifest.columns if '_wo_coverage' in col]
    new_manifest = new_manifest[[col for col in new_manifest.columns if col not in remove_cols]]
    new_manifest.rename(columns={'genome_scale_factor': 'scale_factor'}, inplace=True)

    # save the results into Excel file
    logger.info(f'Saving results to {outdir}.')
    if not isinstance(out_filename, str) and out_filename != '':
        out_filename = 'result.xlsx'
    min_coverage_list = list(set(min_coverage_list))
    min_coverage_list.sort(reverse=True)

    # save the results with different min_coverage
    with pd.ExcelWriter(os.path.join(outdir, out_filename), engine='openpyxl', mode='w') as writer:
        if keep_raw:
            new_manifest.to_excel(writer, sheet_name=f'raw_result', index=False)
        for min_coverage in min_coverage_list:
            temp_output_result = new_manifest.copy()
            temp_output_result['min_coverage'] = min_coverage
            temp_output_result['acceptance_threshold_with_coverage'] = min_coverage * temp_output_result['acceptance_threshold_with_coverage']
            temp_output_result['actual_confidence_with_coverage'] = min_coverage * temp_output_result['actual_confidence_with_coverage']
            temp_output_result['alt_confidence_mut_rate_with_coverage'] = min_coverage * temp_output_result['alt_confidence_mut_rate_with_coverage']
            temp_output_result['in_sample_est'] = (temp_output_result['num_matches'] >= temp_output_result['acceptance_threshold_with_coverage']) \
                                                    & (temp_output_result['num_matches'] != 0) & (temp_output_result['acceptance_threshold_with_coverage'] != 0)
            if not show_all:
                temp_output_result = temp_output_result[temp_output_result['in_sample_est'] == True]
            temp_output_result.to_excel(writer, sheet_name=f'min_coverage{min_coverage}', index=False)
